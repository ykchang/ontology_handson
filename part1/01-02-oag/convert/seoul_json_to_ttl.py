#!/usr/bin/env python3
import argparse, json, re, sys
from datetime import datetime
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, XSD

# Helpers
def yn_to_bool(v):
    if v is None: return None
    s = str(v).strip().upper()
    if s in ("Y","YES","TRUE","T","1"): return True
    if s in ("N","NO","FALSE","F","0"): return False
    return None

def to_int(x):
    try:
        if x is None or x == "":
            return None
        return int(str(x).replace(",", ""))
    except Exception:
        return None

def to_float(x):
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", ""))
    except Exception:
        return None

def is_dt_like(s: str) -> bool:
    s = str(s)
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", s))

def to_iso_datetime(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        if "T" in s:
            return s
        if len(s) == 16:
            from datetime import datetime
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
            return dt.isoformat()
        elif len(s) == 19:
            from datetime import datetime
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return dt.isoformat()
        else:
            return s.replace(" ", "T")
    except Exception:
        return s.replace(" ", "T")

def slugify(s):
    if s is None:
        return None
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"[^0-9A-Za-z\-가-힣_]", "", s)
    return s

def condense_time(iso_t: str) -> str:
    return re.sub(r"[-:]", "", iso_t)

def literal_guess(val):
    if isinstance(val, bool):
        return Literal(val, datatype=XSD.boolean)
    if isinstance(val, int):
        return Literal(val, datatype=XSD.integer)
    if isinstance(val, float):
        return Literal(val, datatype=XSD.decimal)
    s = str(val)
    b = yn_to_bool(s)
    if b is not None:
        return Literal(b, datatype=XSD.boolean)
    i = to_int(s)
    if i is not None and str(i) == s.replace(",",""):
        return Literal(i, datatype=XSD.integer)
    f = to_float(s)
    if f is not None:
        return Literal(f, datatype=XSD.decimal)
    if is_dt_like(s):
        return Literal(to_iso_datetime(s), datatype=XSD.dateTime)
    return Literal(s)

def norm_key(k: str) -> str:
    k = k.strip().replace(".", "_")
    k = re.sub(r"\W+", "_", k, flags=re.UNICODE)
    k = re.sub(r"_+", "_", k).strip("_")
    return k.lower()

def add_kv_all(g: Graph, subj, EX, item: dict, known: set, prefix: str, keep_raw_json: bool):
    for k, v in item.items():
        if k in known:
            continue
        nk = norm_key(k)
        pred = EX[f"{prefix}{nk}"]
        if isinstance(v, (str, int, float, bool)) or v is None:
            if v is None: 
                continue
            g.add((subj, pred, literal_guess(v)))
        elif isinstance(v, (list, dict)):
            if keep_raw_json:
                jpred = EX[f"json_{nk}"]
                g.add((subj, jpred, Literal(json.dumps(v, ensure_ascii=False))))
        else:
            g.add((subj, pred, Literal(str(v))))
    return g

def main():
    ap = argparse.ArgumentParser(description="Seoul citydata_ppltn JSON → TTL (local names, full coverage)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--base", default="http://k.doverenc/onto#")
    ap.add_argument("--place-code", default=None)
    ap.add_argument("--label-lang", default="ko", help="language tag for rdfs:label (default: ko)")
    ap.add_argument("--keep-raw-json", action="store_true", help="store non-scalar values as JSON strings")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    key = next((k for k in data.keys() if "citydata_ppltn" in k), None)
    if not key:
        print("citydata_ppltn 키를 찾을 수 없음", file=sys.stderr); sys.exit(1)
    arr = data.get(key, [])
    if not arr:
        print("citydata_ppltn 배열 비어있음", file=sys.stderr); sys.exit(1)
    item = arr[0]

    EX = Namespace(args.base)
    g = Graph(); g.bind("", EX); g.bind("xsd", XSD); g.bind("rdfs", RDFS)

    area_nm = item.get("AREA_NM")
    area_cd = args.place_code or item.get("AREA_CD") or slugify(area_nm) or "unknown"

    # Place
    place_local = f"place_{area_cd}"
    place_uri = EX[place_local]
    g.add((place_uri, RDF.type, EX.Place))
    if area_nm:
        g.add((place_uri, RDFS.label, Literal(area_nm, lang=args.label_lang)))
    g.add((place_uri, EX["iri"], Literal(str(place_uri))))

    known_place = set()
    def add_know(subj, pred, key, cast=None):
        known_place.add(key)
        val = item.get(key)
        if val is None: return
        if cast == "dt":
            g.add((subj, pred, Literal(to_iso_datetime(val), datatype=XSD.dateTime)))
        elif cast == "int":
            iv = to_int(val); 
            if iv is not None: g.add((subj, pred, Literal(iv, datatype=XSD.integer)))
        elif cast == "float":
            fv = to_float(val); 
            if fv is not None: g.add((subj, pred, Literal(fv, datatype=XSD.decimal)))
        elif cast == "bool":
            bv = yn_to_bool(val)
            if bv is not None: g.add((subj, pred, Literal(bv, datatype=XSD.boolean)))
        else:
            g.add((subj, pred, Literal(val)))

    add_know(place_uri, EX.areaCode, "AREA_CD")
    add_know(place_uri, EX.hasCongestionLevel, "AREA_CONGEST_LVL")
    add_know(place_uri, EX.congestionMessage, "AREA_CONGEST_MSG")
    add_know(place_uri, EX.populationMin, "AREA_PPLTN_MIN", "int")
    add_know(place_uri, EX.populationMax, "AREA_PPLTN_MAX", "int")
    add_know(place_uri, EX.maleRate, "MALE_PPLTN_RATE", "float")
    add_know(place_uri, EX.femaleRate, "FEMALE_PPLTN_RATE", "float")
    for age in ("0","10","20","30","40","50","60","70"):
        k_age = f"PPLTN_RATE_{age}"
        known_place.add(k_age)
        v = item.get(k_age)
        if v is not None:
            fv = to_float(v)
            if fv is not None:
                g.add((place_uri, EX[f"ageRate_{age}"], Literal(fv, datatype=XSD.decimal)))
            else:
                g.add((place_uri, EX[f"ageRate_{age}"], Literal(v)))
    add_know(place_uri, EX.residentRate, "RESNT_PPLTN_RATE", "float")
    add_know(place_uri, EX.nonResidentRate, "NON_RESNT_PPLTN_RATE", "float")
    add_know(place_uri, EX.asOf, "PPLTN_TIME", "dt")
    add_know(place_uri, EX["replace"], "REPLACE_YN", "bool")
    add_know(place_uri, EX.hasForecast, "FCST_YN", "bool")

    add_kv_all(g, place_uri, EX, item, known_place, prefix="attr_", keep_raw_json=args.keep_raw_json)

    # Forecasts
    for fc in (item.get("FCST_PPLTN") or []):
        known_fc = set(["FCST_TIME", "FCST_CONGEST_LVL", "FCST_PPLTN_MIN", "FCST_PPLTN_MAX"])
        t = fc.get("FCST_TIME"); iso_t = to_iso_datetime(t) if t else None
        safe_t = condense_time(iso_t) if iso_t else "unknown"
        f_local = f"forecast_{area_cd}_{safe_t}"
        f_uri = EX[f_local]
        g.add((f_uri, RDF.type, EX.Forecast))
        g.add((f_uri, EX.forPlace, place_uri))
        if iso_t:
            g.add((f_uri, EX.forecastTime, Literal(iso_t, datatype=XSD.dateTime)))
        v = fc.get("FCST_CONGEST_LVL")
        if v is not None:
            g.add((f_uri, EX.forecastCongestionLevel, Literal(v)))
        vmn = to_int(fc.get("FCST_PPLTN_MIN"))
        if vmn is not None:
            g.add((f_uri, EX.forecastPopulationMin, Literal(vmn, datatype=XSD.integer)))
        vmx = to_int(fc.get("FCST_PPLTN_MAX"))
        if vmx is not None:
            g.add((f_uri, EX.forecastPopulationMax, Literal(vmx, datatype=XSD.integer)))
        if area_nm and iso_t:
            human = f"{area_nm} {iso_t.replace('T',' ')[:16]} 예측"
            g.add((f_uri, RDFS.label, Literal(human, lang=args.label_lang)))
        g.add((f_uri, EX["iri"], Literal(str(f_uri))))
        add_kv_all(g, f_uri, EX, fc, known_fc, prefix="fattr_", keep_raw_json=args.keep_raw_json)

    # RESULT → Response
    result = data.get("RESULT") or data.get("SeoulRtd.RESULT") or {}
    if "RESULT" in result:
        result = result["RESULT"]
    if result:
        ts = to_iso_datetime(item.get("PPLTN_TIME") or "unknown")
        safe_ts = condense_time(ts) if ts else "unknown"
        r_local = f"response_{area_cd}_{safe_ts}"
        resp_uri = EX[r_local]
        g.add((resp_uri, RDF.type, EX.Response))
        g.add((resp_uri, EX["iri"], Literal(str(resp_uri))))
        # Known
        for k, pred_name in [("RESULT.CODE","resultCode"), ("RESULT.MESSAGE","resultMessage")]:
            if k in result:
                g.add((resp_uri, EX[pred_name], literal_guess(result[k])))
        g.add((resp_uri, EX["aboutPlace"], place_uri))
        # Preserve others
        known_resp = set(["RESULT.CODE","RESULT.MESSAGE"])
        add_kv_all(g, resp_uri, EX, result, known_resp, prefix="rattr_", keep_raw_json=args.keep_raw_json)

    # Vocabulary labels
    for term, label in [
        (EX.Place, "Place"), (EX.Forecast, "Forecast"), (EX.Response, "Response"),
        (EX.areaCode, "area code"),
        (EX.hasCongestionLevel, "congestion level"),
        (EX.congestionMessage, "congestion message"),
        (EX.populationMin, "population min"), (EX.populationMax, "population max"),
        (EX.maleRate, "male rate"), (EX.femaleRate, "female rate"),
        (EX.residentRate, "resident rate"), (EX.nonResidentRate, "non-resident rate"),
        (EX.asOf, "as of time"),
        (EX["replace"], "replace flag"), (EX.hasForecast, "has forecast flag"),
        (EX.forPlace, "for place"), (EX.forecastTime, "forecast time"),
        (EX.forecastCongestionLevel, "forecast congestion level"),
        (EX.forecastPopulationMin, "forecast population min"),
        (EX.forecastPopulationMax, "forecast population max"),
        (EX["resultCode"], "result code"), (EX["resultMessage"], "result message"),
        (EX["aboutPlace"], "about place"),
        (EX["iri"], "string form of IRI"),
    ]:
        g.add((term, RDFS.label, Literal(label)))

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(g.serialize(format="turtle"))

if __name__ == "__main__":
    main()
