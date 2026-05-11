#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
질문 해석 단계 예제

- Ollama의 qwen2.5 계열 모델을 사용해
  한국어 자연어 질문을 구조화된 JSON 파라미터로 변환.
- 출력 JSON은 SPARQL 템플릿에 그대로 주입할 수 있는 형태로 설계.
"""

import json
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

import ollama  # pip install ollama


# Ollama에서 사용하는 qwen2.5b 모델 이름 (로컬 환경에 맞게 수정 가능)
QWEN_MODEL_NAME = "qwen2.5:3b"  # 예시: qwen2.5 3B; 실제 설치된 이름으로 변경하세요.


# 온톨로지/도메인에 맞춘 질문 해석 결과 스키마 -------------------------------

@dataclass
class EVChargerConstraints:
    need_ev_charger: bool = False
    min_available: int = 0          # 최소 사용가능 충전기 개수
    max_busy_ratio: Optional[float] = None  # 혼잡도 상한 (0.5 = 50%)


@dataclass
class TimeSlot:
    # 날짜까지 엄밀하게 쓰고 싶으면 iso_datetime 사용
    time_of_day: Optional[str] = None   # "20:00" 같은 HH:MM 문자열
    iso_datetime: Optional[str] = None  # 선택: ISO 8601, 없으면 None


@dataclass
class AreaSelection:
    name: Optional[str] = None      # "강남역"
    area_code: Optional[str] = None # "POI014" 등, 매핑 가능할 때만 세팅


@dataclass
class QueryInterpretation:
    original_question: str
    intent: str                     # "ev_charger_recommendation" 등
    area: AreaSelection
    time: TimeSlot
    constraints: EVChargerConstraints


# 사용자 질문 → Ollama 프롬프트 ---------------------------------------------

SYSTEM_PROMPT = """
당신은 서울시 실시간 도시데이터 온톨로지를 사용하는 의사결정 지원 시스템의
"질문 해석기"입니다.

역할:
- 한국어 자연어 질문을 읽고, SPARQL에 넘겨줄 파라미터를 JSON으로 추출합니다.
- EV 충전 추천/혼잡도/시간/지역에 관한 정보만 구조화합니다.
- JSON만 출력하며, 설명 문장은 절대 쓰지 않습니다.

JSON 스키마 (반드시 이 구조만 사용하십시오):

{
  "intent": "string",                 // 예: "ev_charger_recommendation"
  "area": {
    "name": "string or null",         // 사용자가 언급한 지역 이름 (예: "강남역")
    "area_code": "string or null"     // 알고 있다면 코드, 모르면 null
  },
  "time": {
    "time_of_day": "string or null",  // "20:00" 형태, 또는 null
    "iso_datetime": "string or null"  // "2025-11-18T20:00:00" 같은 전체 시각, 모르면 null
  },
  "constraints": {
    "ev_charger": {
      "need_ev_charger": true,
      "min_available": 1,
      "max_busy_ratio": 0.5
    }
  }
}

규칙:
- 질문이 EV 충전소 추천에 해당하면 intent는 "ev_charger_recommendation"으로 합니다.
- "대기 없이 충전" 같은 표현이 있으면 ev_charger.min_available = 1 이상으로 둡니다.
- "혼잡하지 않은 곳"이라고 하면 ev_charger.max_busy_ratio = 0.5 로 둡니다.
- 사용자가 지역 이름을 말하면 area.name에 그대로 넣고, area_code는 모르면 null로 둡니다.
- 시각(오후 8시, 저녁 8시 등)이 언급되면 time.time_of_day를 "20:00" 형태로 넣습니다.
- 날짜가 명시되지 않으면 iso_datetime은 null로 둡니다.
- 확실하지 않은 값은 추측하지 말고 null 또는 보수적인 기본값으로 둡니다.
- 반드시 JSON만, 추가 텍스트 없이 출력하십시오.
"""


def call_ollama_for_interpretation(question: str) -> Dict[str, Any]:
    """
    Ollama에 qwen2.5 모델을 호출하여 질문을 JSON 구조로 해석.
    """
    response = ollama.chat(
        model=QWEN_MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    content = response["message"]["content"]

    # 모델이 코드블록(````json`)을 붙이는 경우를 대비해 JSON 부분만 추출
    content = content.strip()
    # ```json ... ``` 제거
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*", "", content)
        content = re.sub(r"```$", "", content).strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 최소한의 회복: 중괄호 기준으로 잘라서 재시도
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            raise
        data = json.loads(m.group(0))

    return data


# 도메인 매핑/후처리 ---------------------------------------------------------

AREA_NAME_TO_CODE = {
    "강남역": "POI014",
    # 필요 시 다른 POI도 여기에 추가
}


def postprocess_interpretation(raw: Dict[str, Any], question: str) -> QueryInterpretation:
    """
    LLM이 준 JSON을 내부 dataclass로 정제 + area_code 매핑 등 후처리.
    """
    # area 처리
    area_obj = raw.get("area") or {}
    area_name = area_obj.get("name")
    area_code = area_obj.get("area_code")

    if area_name and not area_code:
        area_code = AREA_NAME_TO_CODE.get(area_name)

    area = AreaSelection(name=area_name, area_code=area_code)

    # time 처리
    time_obj = raw.get("time") or {}
    time = TimeSlot(
        time_of_day=time_obj.get("time_of_day"),
        iso_datetime=time_obj.get("iso_datetime"),
    )

    # constraints 처리
    cons_obj = (raw.get("constraints") or {}).get("ev_charger") or {}
    evc = EVChargerConstraints(
        need_ev_charger=bool(cons_obj.get("need_ev_charger", False)),
        min_available=int(cons_obj.get("min_available", 0)),
        max_busy_ratio=cons_obj.get("max_busy_ratio"),
    )

    intent = raw.get("intent") or "unknown"

    return QueryInterpretation(
        original_question=question,
        intent=intent,
        area=area,
        time=time,
        constraints=evc,
    )


def interpret_question(question: str) -> QueryInterpretation:
    """
    외부에서 호출하는 진입점:
    - 한국어 질문을 받아
    - Ollama(qwen2.5b)로 질의 해석
    - 내부 dataclass(QueryInterpretation) 형태로 반환
    """
    raw = call_ollama_for_interpretation(question)
    interpretation = postprocess_interpretation(raw, question)
    return interpretation


# 간단한 사용 예 ------------------------------------------------------------

if __name__ == "__main__":
    sample_question = "오후 8시, 강남역 근처에서 대기 없이 전기차 충전을 할 수 있고, 혼잡하지 않은 곳을 찾고 싶습니다."
    result = interpret_question(sample_question)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
