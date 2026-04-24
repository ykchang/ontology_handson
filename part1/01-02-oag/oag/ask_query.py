#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
질문 → LLM(의도/파라미터/작업유형) → SPARQL 템플릿 실행 → LLM 한국어 서술

작업유형(task):
- list_by_level:  특정 혼잡도(level) 시간대 나열  (기존 동작)
- peak_time:      가장 붐빌(피크) 시간 1건(또는 상위 N)  ← 추가

데이터 전제: Fuseki named graph = http://k.fc/onto (강남역 TTL 업로드 완료)

준비:
  ollama serve
  ollama pull qwen3
설치:
  pip install -U langchain-core langchain-community langchain-ollama requests
"""

import os
import json
import argparse
import re
from string import Template
from typing import List, Optional, Dict, Any

import requests
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama  # 권장 패키지

# ----------------------------
# 환경설정
# ----------------------------
SPARQL_ENDPOINT = os.environ.get("SPARQL_ENDPOINT", "http://localhost:3030/fc/query")
GRAPH_IRI       = os.environ.get("GRAPH_IRI",       "http://k.fc/onto")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "qwen3")  # 로컬 pull 된 모델명

def llm_client() -> ChatOllama:
    return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)

# ----------------------------
# SPARQL 템플릿
# ----------------------------
RESOLVE_PLACE_BY_LABEL = Template("""
PREFIX :    <http://k.fc/onto#>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
SELECT ?p
WHERE {
  GRAPH <${GRAPH}> {
    ?p a :Place ; rdfs:label ?label .
    FILTER(lang(?label) = "ko")
    FILTER(STR(?label) = "${LABEL}")
  }
}
LIMIT 1
""")

# level 시간대 나열
FORECAST_TIMES_BY_LEVEL = Template("""
PREFIX :    <http://k.fc/onto#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?t
WHERE {
  GRAPH <${GRAPH}> {
    ?f a :Forecast ;
       :forPlace <${PLACE}> ;
       :forecastCongestionLevel "${LEVEL}" ;
       :forecastTime ?t .
    ${RANGE_FILTER}
  }
}
ORDER BY ?t
""")

# 피크 시간(가장 붐빔 = 인구상한 최대) 상위 N
FORECAST_PEAK_TIME = Template("""
PREFIX :    <http://k.fc/onto#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?t ?lvl ?min ?max
WHERE {
  GRAPH <${GRAPH}> {
    ?f a :Forecast ;
       :forPlace <${PLACE}> ;
       :forecastTime ?t ;
       :forecastCongestionLevel ?lvl ;
       :forecastPopulationMin ?min ;
       :forecastPopulationMax ?max .
    ${RANGE_FILTER}
    ${LEVEL_FILTER}
  }
}
ORDER BY DESC(?max) DESC(?min) ?t
LIMIT ${LIMIT}
""")

# ----------------------------
# SPARQL 호출
# ----------------------------
def call_sparql(query: str) -> Dict[str, Any]:
    r = requests.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def resolve_place_iri(place_label: str) -> Optional[str]:
    q = RESOLVE_PLACE_BY_LABEL.substitute(GRAPH=GRAPH_IRI, LABEL=place_label)
    js = call_sparql(q)
    rows = js.get("results", {}).get("bindings", [])
    return rows[0]["p"]["value"] if rows else None

# ----------------------------
# LLM ①: 질문 → 파라미터/작업유형(JSON)
# ----------------------------
# 작업유형 추가: task ∈ { "list_by_level", "peak_time" }
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "enum": ["list_by_level", "peak_time"],
                 "description": "질문 의도에 맞는 작업유형"},
        "place_label": {"type": "string", "description": "장소 라벨(ko). 예: 강남역"},
        "level": {"type": "string", "description": "혼잡도 단계(없으면 생략 또는 '붐빔')."},
        "start_iso": {"type": "string", "description": "시작시각 ISO(옵션)"},
        "end_iso": {"type": "string", "description": "종료시각 ISO(옵션)"},
        "limit": {"type": "integer", "description": "peak_time 상위 N (기본 1)"}
    },
    "required": ["task", "place_label"],
    "additionalProperties": False
}
INTENT_SCHEMA_TXT = json.dumps(INTENT_SCHEMA, ensure_ascii=False)

intent_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "너는 한국어 질문에서 질의 슬롯을 추출하는 어시스턴트다. "
     "반드시 **순수 JSON만** 출력하라(코드펜스/주석/설명 금지). "
     "질문 의도에 따라 task를 결정하라: "
     "- '가장', '최고', '피크' 등 최대를 묻는 표현 → task='peak_time' "
     "- 특정 혼잡도 시간대 나열 요청 → task='list_by_level' "
     "혼잡도 level이 없으면 기본 '붐빔'을 사용해도 되지만, peak_time에서는 level이 필수는 아니다. "
     "시간 범위가 없으면 start_iso/end_iso/limit 는 생략 가능, peak_time의 limit 기본은 1이다."),
    ("user", "질문: {question}\nJSON 스키마: {schema}")
])

def _extract_json_snippet(text: str) -> str:
    if not text:
        return ""
    t = text.strip().replace("```json", "```").strip("` \n\t")
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    return m.group(0) if m else ""

def _fallback_intent_from_question(q: str) -> Dict[str, Any]:
    # 매우 간단한 규칙: '가장|최고|피크' → peak_time, 아니면 list_by_level
    place = re.search(r"([ㄱ-ㅎ가-힣A-Za-z0-9]+역)", q)
    place_label = place.group(1) if place else "강남역"
    if re.search(r"(가장|최고|피크|peak|max)", q):
        return {"task": "peak_time", "place_label": place_label, "limit": 1}
    return {"task": "list_by_level", "place_label": place_label, "level": "붐빔"}

def parse_intent(question: str) -> Dict[str, Any]:
    llm = llm_client()
    messages = intent_prompt.format_messages(question=question, schema=INTENT_SCHEMA_TXT)
    out = llm.invoke(messages)
    raw = (out.content or "").strip()
    # 1차 직접
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 2차 스니펫
    s = _extract_json_snippet(raw)
    if s:
        try:
            return json.loads(s)
        except Exception:
            pass
    # 3차 접두 'json' 제거
    t = raw
    if t.lower().startswith("json"):
        t = t[4:].strip()
        try:
            return json.loads(t)
        except Exception:
            pass
    # 4차 폴백
    return _fallback_intent_from_question(question)

# ----------------------------
# 질의 실행
# ----------------------------
def query_times_by_level(place_iri: str, level: str,
                         start_iso: Optional[str], end_iso: Optional[str]) -> List[str]:
    range_filter = ""
    if start_iso and end_iso:
        range_filter = f'FILTER(?t >= "{start_iso}"^^xsd:dateTime && ?t <= "{end_iso}"^^xsd:dateTime)'
    q = FORECAST_TIMES_BY_LEVEL.substitute(
        GRAPH=GRAPH_IRI, PLACE=place_iri, LEVEL=level, RANGE_FILTER=range_filter
    )
    js = call_sparql(q)
    rows = js.get("results", {}).get("bindings", [])
    return [r["t"]["value"] for r in rows]

def query_peak_time(place_iri: str,
                    start_iso: Optional[str], end_iso: Optional[str],
                    level: Optional[str], limit: int = 1) -> List[Dict[str, str]]:
    range_filter = ''
    if start_iso and end_iso:
        range_filter = f'FILTER(?t >= "{start_iso}"^^xsd:dateTime && ?t <= "{end_iso}"^^xsd:dateTime)'
    level_filter = f'FILTER(?lvl = "{level}")' if level else ''
    q = FORECAST_PEAK_TIME.substitute(
        GRAPH=GRAPH_IRI, PLACE=place_iri,
        RANGE_FILTER=range_filter, LEVEL_FILTER=level_filter,
        LIMIT=max(1, int(limit or 1))
    )
    js = call_sparql(q)
    rows = js.get("results", {}).get("bindings", [])
    out = []
    for r in rows:
        out.append({
            "time": r["t"]["value"],
            "level": r["lvl"]["value"],
            "min": r["min"]["value"],
            "max": r["max"]["value"]
        })
    return out

# ----------------------------
# LLM ②: 최종 서술
# ----------------------------
answer_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "너는 질의 결과를 한국어로 간결하고 정확히 설명하는 어시스턴트다. "
     "사실(시간/값)은 그대로 유지하고, 불필요한 수사는 피하라. "
     "값이 없으면 이유와 함께 '조회되지 않았음'이라고 말하라."),
    ("user",
     "작업유형: {task}\n"
     "장소 라벨: {place_label}\n"
     "혼잡도 단계(있으면): {level}\n"
     "시간범위: {time_range}\n"
     "결과 JSON: {result_json}")
])

def render_answer(task: str, place_label: str, level: Optional[str],
                  start_iso: Optional[str], end_iso: Optional[str],
                  result_obj: Any) -> str:
    llm = llm_client()
    time_range = f"{start_iso} ~ {end_iso}" if (start_iso and end_iso) else "지정 없음"
    messages = answer_prompt.format_messages(
        task=task,
        place_label=place_label,
        level=level if level else "미지정",
        time_range=time_range,
        result_json=json.dumps(result_obj, ensure_ascii=False)
    )
    out = llm.invoke(messages)
    return out.content

# ----------------------------
# 엔드투엔드
# ----------------------------
def answer_question(question: str) -> str:
    # 1) 의도/파라미터/작업유형
    intent = parse_intent(question)
    task       = intent.get("task", "list_by_level")
    place_lbl  = intent.get("place_label", "강남역")
    level      = intent.get("level")
    start_iso  = intent.get("start_iso")
    end_iso    = intent.get("end_iso")
    limit      = intent.get("limit", 1)

    # 2) 라벨→IRI
    place_iri = resolve_place_iri(place_lbl)
    if not place_iri:
        return f"{place_lbl} 라벨에 해당하는 장소를 찾지 못했습니다."

    # 3) SPARQL 실행
    if task == "peak_time":
        result = query_peak_time(place_iri, start_iso, end_iso, level, limit)
    else:
        # 기본: list_by_level (level 없으면 '붐빔'을 기본으로 보정)
        lvl = level or "붐빔"
        times = query_times_by_level(place_iri, lvl, start_iso, end_iso)
        result = times

    # 4) LLM 서술
    return render_answer(task, place_lbl, level, start_iso, end_iso, result)

# ----------------------------
# CLI
# ----------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="강남역 예측 질의(Ollama Qwen3 + SPARQL 템플릿, 의도 분기 포함)")
    parser.add_argument("--question", default="강남역의 가장 붐빌것 같은 시간은 언제인가요?")
    parser.add_argument("--sparql-endpoint", default=SPARQL_ENDPOINT)
    parser.add_argument("--graph-iri", default=GRAPH_IRI)
    parser.add_argument("--ollama-base", default=OLLAMA_BASE_URL)
    parser.add_argument("--ollama-model", default=OLLAMA_MODEL)
    args = parser.parse_args()

    # 실행 중 덮어쓰기 허용
    SPARQL_ENDPOINT = args.sparql_endpoint
    GRAPH_IRI       = args.graph_iri
    OLLAMA_BASE_URL = args.ollama_base
    OLLAMA_MODEL    = args.ollama_model

    print(answer_question(args.question))

