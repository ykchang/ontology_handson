#!/usr/bin/env python3
# Usage:
#   python3 text_to_sosa_ttl.py \
#       --text-file result.txt \
#       --sensor "http://k.fc/onto/cctv#CCTV_1234" \
#       --time "2025-10-17T14:30:00Z" \
#       --out obs.ttl
#

import argparse, json, re, sys, uuid, requests
from pathlib import Path

OLLAMA_HOST = "http://localhost:11434/api/generate"
MODEL = "qwen3:8b"  

PROMPT = """You convert an English paragraph about a city scene into a compact JSON with three fields.
Allowed values:
- weather: one of [Clear, Cloudy, Rain, Snow, Fog, Unknown]
- pedestrian_congestion: one of [VeryCrowded, Crowded, Normal, Sparse, Unknown]
- traffic_volume: one of [Heavy, Moderate, Light, Unknown]

Rules:
1) Decide only from the paragraph. Do not invent facts.
2) Return JSON only, no prose.
Example:
{"weather":"Clear","pedestrian_congestion":"VeryCrowded","traffic_volume":"Heavy"}

Paragraph:
<<<TEXT>>>"""

PFX = {
    "base":   "http://k.fc/onto/",
    "cctv":   "http://k.fc/onto/cctv#",
    "sosa":   "http://www.w3.org/ns/sosa/",
    "xsd":    "http://www.w3.org/2001/XMLSchema#"
}

def call_llm(text: str) -> dict:
    payload = {
        "model": MODEL,
        "prompt": PROMPT.replace("<<<TEXT>>>", text.strip()),
        "stream": False,
        "options": {"temperature": 0.0}
    }
    r = requests.post(OLLAMA_HOST, json=payload, timeout=120)
    r.raise_for_status()
    msg = r.json().get("response", "").strip()
    try:
        data = json.loads(msg)
        return data
    except json.JSONDecodeError:
        # 최후 보정: 키워드 히ュー리스틱
        t = text.lower()
        weather = ("Rain" if re.search(r"\brain|umbrella|wet road", t) else
                   "Snow" if "snow" in t else
                   "Fog" if "fog" in t else
                   "Cloudy" if re.search(r"overcast|cloud", t) else
                   "Clear" if re.search(r"clear|sunny|good visibility", t) else "Unknown")
        ped = ("VeryCrowded" if re.search(r"very crowded|heavily|packed", t) else
               "Crowded" if "crowded" in t else
               "Sparse" if re.search(r"sparse|few pedestrians", t) else
               "Normal" if "normal" in t else "Unknown")
        traf = ("Heavy" if re.search(r"heavy|jam|congestion", t) else
                "Light" if "light" in t else
                "Moderate" if "moderate" in t else "Unknown")
        return {"weather": weather, "pedestrian_congestion": ped, "traffic_volume": traf}

def norm_enum(v: str, allowed):
    v = (v or "").strip()
    for a in allowed:
        if v.lower() == a.lower(): return a
    # 관용 매핑
    table = {
        "very crowded":"VeryCrowded","very_crowded":"VeryCrowded",
        "crowd":"Crowded","congested":"Heavy","heavy traffic":"Heavy",
        "moderate traffic":"Moderate","light traffic":"Light"
    }
    return table.get(v.lower(), "Unknown")

def to_ttl(json_obj: dict, sensor_iri: str, iso_time: str) -> str:
    weather = norm_enum(json_obj.get("weather"), ["Clear","Cloudy","Rain","Snow","Fog","Unknown"])
    ped = norm_enum(json_obj.get("pedestrian_congestion"), ["VeryCrowded","Crowded","Normal","Sparse","Unknown"])
    traf = norm_enum(json_obj.get("traffic_volume"), ["Heavy","Moderate","Light","Unknown"])

    # 로컬 네임 사용을 위해 QName 리소스 준비
    obs_id = lambda p: p + "_" + uuid.uuid4().hex[:8]
    o_weather = f":Obs_Weather_{uuid.uuid4().hex[:6]}"
    o_cong    = f":Obs_Congestion_{uuid.uuid4().hex[:6]}"
    o_traffic = f":Obs_Traffic_{uuid.uuid4().hex[:6]}"

    lines = []
    lines.append(f"@prefix :     <{PFX['base']}> .")
    lines.append(f"@prefix cctv: <{PFX['cctv']}> .")
    lines.append(f"@prefix sosa: <{PFX['sosa']}> .")
    lines.append(f"@prefix xsd:  <{PFX['xsd']}> .\n")

    # 혼잡도
    lines += [
        f"{o_cong} a sosa:Observation ;",
        f"    sosa:observedProperty :Congestion ;",
        f"    sosa:hasResult :{ped} ;",
        f"    sosa:madeBySensor <{sensor_iri}> ;",
        f"    sosa:resultTime \"{iso_time}\"^^xsd:dateTime .\n"
    ]
    # 교통량
    lines += [
        f"{o_traffic} a sosa:Observation ;",
        f"    sosa:observedProperty :TrafficVolume ;",
        f"    sosa:hasResult :{traf} ;",
        f"    sosa:madeBySensor <{sensor_iri}> ;",
        f"    sosa:resultTime \"{iso_time}\"^^xsd:dateTime .\n"
    ]
    # 날씨
    lines += [
        f"{o_weather} a sosa:Observation ;",
        f"    sosa:observedProperty :WeatherCondition ;",
        f"    sosa:hasResult :{weather} ;",
        f"    sosa:madeBySensor <{sensor_iri}> ;",
        f"    sosa:resultTime \"{iso_time}\"^^xsd:dateTime ."
    ]
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description="Natural language -> Qwen3 normalization -> SOSA TTL")
    ap.add_argument("--text", help="Input paragraph (English)")
    ap.add_argument("--text-file", help="Path to a file containing the paragraph")
    ap.add_argument("--sensor", required=True, help="Sensor IRI (e.g., http://k.fc/onto/cctv#CCTV_1234)")
    ap.add_argument("--time", required=True, help="Observation time ISO8601 (e.g., 2025-10-17T14:30:00Z)")
    ap.add_argument("--out", default="observations.ttl", help="Output TTL path")
    args = ap.parse_args()

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        print("Error: --text or --text-file is required.", file=sys.stderr)
        sys.exit(2)

    data = call_llm(text)
    ttl = to_ttl(data, args.sensor, args.time)
    Path(args.out).write_text(ttl, encoding="utf-8")
    print(ttl)

if __name__ == "__main__":
    main()
