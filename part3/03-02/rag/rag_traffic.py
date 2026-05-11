import os
import json
import requests
from typing import Tuple, Optional

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

# 설정
FUSEKI_SPARQL_ENDPOINT = "http://localhost:3030/fc/sparql"
OLLAMA_MODEL_NAME = "qwen3:8b"

def get_llm(temperature: float = 0.0) -> ChatOllama:
    return ChatOllama(model=OLLAMA_MODEL_NAME, temperature=temperature)

def extract_area_name_and_now_flag(question: str) -> Tuple[str, bool]:
    llm = get_llm()
    template = """
당신은 사용자의 자연어 질문에서 다음 정보를 JSON으로 추출합니다:
- region : 한국어로 된 장소명 (예: "강남역") 또는 빈 문자열
- as_of_now : 사용자가 "지금", "현재", "최근" 등의 키워드를 사용했는지 여부 (true 또는 false)

질문: "{question}"
출력 형식: {{"region": "<지역명>", "as_of_now": <true 또는 false>}}
"""
    prompt = ChatPromptTemplate.from_template(template)
    messages = prompt.format_messages(question=question)
    resp = llm.invoke(messages)
    try:
        data = json.loads(resp.content.strip())
        region = data.get("region", "").strip()
        as_of_now = bool(data.get("as_of_now", False))
    except json.JSONDecodeError:
        region = ""
        as_of_now = False
    return region, as_of_now

def query_status_for_area(area_name: str, recent_only: bool = False) -> dict:
    ns = "http://k.fc/onto/citydata#"

    # 최근성 필터: NOW() 사용 가능 여부에 따라 구성
    time_filter = ""
    if recent_only:
        # SPARQL 1.1 규격에 따라 NOW() 사용 가능  [oai_citation:0‡w3.org](https://www.w3.org/TR/sparql11-query/?utm_source=chatgpt.com)
        # 다만 서버에 따라 NOW()-"PT15M"^^xsd:duration 지원 여부 확인 필요
        time_filter = """
          FILTER(?time >= (NOW() - "PT15M"^^xsd:duration))
        """

    traffic_query = f"""
PREFIX ns1: <{ns}>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?areaName ?time ?speed ?index
WHERE {{
  ?area a ns1:Area ;
        ns1:name ?areaName ;
        ns1:hasStatus ?status .

  FILTER(STR(?areaName) = "{area_name}") .
  ?status a ns1:TrafficStatus ;
          ns1:observationTime ?time ;
          ns1:speed ?speed ;
          ns1:trafficIndex ?index .
  {time_filter}
}}
ORDER BY DESC(?time)
LIMIT 1
"""

    population_query = f"""
PREFIX ns1: <{ns}>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?areaName ?time ?congestion ?pmin ?pmax
WHERE {{
  ?area a ns1:Area ;
        ns1:name ?areaName ;
        ns1:hasStatus ?status .

  FILTER(STR(?areaName) = "{area_name}") .
  ?status a ns1:PopulationStatus ;
          ns1:observationTime ?time ;
          ns1:congestionLevel ?congestion ;
          ns1:populationMin ?pmin ;
          ns1:populationMax ?pmax .
  {time_filter}
}}
ORDER BY DESC(?time)
LIMIT 1
"""

    def run_sparql(q: str) -> dict:
        resp = requests.get(
            FUSEKI_SPARQL_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    result = {"area_name": area_name, "traffic": None, "population": None}

    # 교통 상태 조회
    traffic_data = run_sparql(traffic_query)
    t_bindings = traffic_data.get("results", {}).get("bindings", [])
    if t_bindings:
        row = t_bindings[0]
        result["traffic"] = {
            "observationTime": row["time"]["value"],
            "speed": float(row["speed"]["value"]),
            "trafficIndex": row["index"]["value"],
        }

    # 인구 혼잡 조회
    pop_data = run_sparql(population_query)
    p_bindings = pop_data.get("results", {}).get("bindings", [])
    if p_bindings:
        row = p_bindings[0]
        result["population"] = {
            "observationTime": row["time"]["value"],
            "congestionLevel": row["congestion"]["value"],
            "populationMin": int(float(row["pmin"]["value"])),
            "populationMax": int(float(row["pmax"]["value"])),
        }

    return result

def build_context_from_ontology(result: dict) -> str:
    area = result.get("area_name", "알 수 없는 지역")
    lines = [f"[온톨로지 기반 도시데이터 요약] 지역: {area}"]

    traffic = result.get("traffic")
    if traffic:
        lines.append(
            f"- 교통 상태(TrafficStatus): {traffic['observationTime']} 기준, 속도 {traffic['speed']} km/h, 지수 {traffic['trafficIndex']}"
        )
    else:
        lines.append("- 교통 상태(TrafficStatus): 최근 데이터 없음")

    population = result.get("population")
    if population:
        lines.append(
            f"- 인구 혼잡(PopulationStatus): {population['observationTime']} 기준, 혼잡도 {population['congestionLevel']}, 추정 인구 {population['populationMin']}~{population['populationMax']}명"
        )
    else:
        lines.append("- 인구 혼잡(PopulationStatus): 최근 데이터 없음")

    return "\n".join(lines)

def answer_question_with_ontology_rag(question: str) -> str:
    area_name, as_of_now = extract_area_name_and_now_flag(question)
    if not area_name:
        return "질문에서 조회할 지역명을 인식하지 못했습니다."

    retrieved = query_status_for_area(area_name, recent_only=as_of_now)
    # 최근 데이터가 없는 경우
    if not retrieved["traffic"] and as_of_now:
        recent_time = retrieved.get("traffic") and retrieved["traffic"].get("observationTime")
        return f"‘{area_name}’에 대해 최근 15분 이내 교통 상태 데이터가 없습니다. 가장 최근 기록은 {recent_time or '없음'}입니다."

    context = build_context_from_ontology(retrieved)

    llm = get_llm()
    template = """
당신은 서울시 실시간 도시데이터 온톨로지를 기반으로 답변하는 도우미입니다.
아래는 SPARQL로 조회된 최신 상태입니다.

[사용자 질문]
{question}

[조회 결과 요약]
{context}

위 정보를 바탕으로 답변하세요.
"""
    prompt = ChatPromptTemplate.from_template(template)
    messages = prompt.format_messages(question=question, context=context)
    resp = llm.invoke(messages)
    return resp.content.strip()

if __name__ == "__main__":
    user_question = input("질문을 입력하세요: ")
    answer = answer_question_with_ontology_rag(user_question)
    print("답변:", answer)
