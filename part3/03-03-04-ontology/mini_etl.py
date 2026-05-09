#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
서울 열린데이터광장 citydata JSON → RDF/온톨로지 변환 스크립트

- SEOUL_API_KEY 환경변수에서 API 키를 읽음
- http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/json/citydata/1/6/{AREA_NM}
  으로 호출
- 변환 결과를 TTL로 출력 후
  - --output-file 이 주어지면 파일로 저장
  - --fuseki-endpoint 가 주어지면 해당 URL로 POST (Graph Store Protocol)
"""

import os
import sys
import json
import argparse
from urllib.parse import quote

import requests
from rdflib import Graph, Namespace, URIRef, BNode, Literal
from rdflib.namespace import RDF, RDFS, XSD

# ------------------------------
# 네임스페이스 정의
# ------------------------------

EX = Namespace("http://k.fc/onto/city#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
WKT_DATATYPE = GEO.wktLiteral


# ------------------------------
# 유틸 함수들
# ------------------------------

def normalize_id(value: str) -> str:
    """URI suffix로 쓰기 위해 값 정규화."""
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s:
        s = "unknown"
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def normalize_predicate(key: str, prefix: str = "") -> URIRef:
    """JSON 키를 RDF 프로퍼티 URI로 변환."""
    base = []
    if prefix:
        base.append(prefix)
    for ch in str(key):
        if ch.isalnum():
            base.append(ch)
        else:
            base.append("_")
    return EX["".join(base)]


def to_xsd_datetime_literal(dt_str: str):
    """문자열을 xsd:dateTime Literal로 최대한 변환. 실패하면 그냥 문자열 Literal."""
    if dt_str is None:
        return None
    s = str(dt_str).strip()
    if not s:
        return None

    # 형태1: "2025-11-17 22:35"
    if len(s) == 16 and s[4] == "-" and s[7] == "-" and s[10] == " " and s[13] == ":":
        iso = s.replace(" ", "T") + ":00"
        return Literal(iso, datatype=XSD.dateTime)

    # 형태2: "2025-11-17 22:35:28"
    if len(s) == 19 and s[4] == "-" and s[7] == "-" and s[10] == " " and s[13] == ":" and s[16] == ":":
        iso = s.replace(" ", "T")
        return Literal(iso, datatype=XSD.dateTime)

    # 형태3: "202511172300" (YYYYMMDDHHMM)
    if len(s) == 12 and s.isdigit():
        iso = f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:00"
        return Literal(iso, datatype=XSD.dateTime)

    # 형태4: "20251117" (YYYYMMDD) – date로만 취급
    if len(s) == 8 and s.isdigit():
        iso = f"{s[0:4]}-{s[4:6]}-{s[6:8]}T00:00:00"
        return Literal(iso, datatype=XSD.dateTime)

    # 실패 시 string literal
    return Literal(s)


def add_point_geometry(graph: Graph, subject: URIRef, lon: str, lat: str):
    """POINT(lon lat) GeoSPARQL geometry 추가."""
    if lon in (None, "") or lat in (None, ""):
        return
    try:
        x = float(lon)
        y = float(lat)
    except Exception:
        return

    geom = BNode()
    graph.add((subject, GEO.hasGeometry, geom))
    graph.add((geom, RDF.type, GEO.Point))
    wkt = f"POINT ({x} {y})"
    graph.add((geom, GEO.asWKT, Literal(wkt, datatype=WKT_DATATYPE)))


def add_linestring_geometry_from_xylist(graph: Graph, subject: URIRef, xylist: str):
    """
    XYLIST: "lon_lat|lon_lat|..." 형태를 LINESTRING WKT로 변환.
    """
    if not xylist:
        return
    parts = str(xylist).split("|")
    coords = []
    for p in parts:
        if "_" not in p:
            continue
        lon_str, lat_str = p.split("_", 1)
        try:
            x = float(lon_str)
            y = float(lat_str)
            coords.append(f"{x} {y}")
        except Exception:
            continue
    if not coords:
        return
    geom = BNode()
    graph.add((subject, GEO.hasGeometry, geom))
    graph.add((geom, RDF.type, GEO.LineString))
    wkt = "LINESTRING (" + ", ".join(coords) + ")"
    graph.add((geom, GEO.asWKT, Literal(wkt, datatype=WKT_DATATYPE)))


def add_all_json_as_generic(graph: Graph, subject: URIRef, data, prefix: str = "json"):
    """
    모든 JSON 키를 빠짐없이 RDF로 보존하기 위한 generic 매핑.
    - 키 이름은 :json_<KEY> 형태로 프로퍼티로 변환
    - dict → blank node 재귀
    - list → 각 요소를 동일 프로퍼티로 추가
    """
    if isinstance(data, dict):
        for key, value in data.items():
            pred = normalize_predicate(key, prefix + "_")
            if isinstance(value, (dict, list)):
                bn = BNode()
                graph.add((subject, pred, bn))
                add_all_json_as_generic(graph, bn, value, prefix=prefix + "_" + key)
            else:
                if value is not None:
                    graph.add((subject, pred, Literal(str(value))))
    elif isinstance(data, list):
        for item in data:
            add_all_json_as_generic(graph, subject, item, prefix=prefix)
    else:
        # primitive 값은 이미 상위 dict에서 처리됨
        pass


# ------------------------------
# 섹션별 도메인 매핑 함수들
# ------------------------------

def map_area(graph: Graph, citydata: dict) -> URIRef:
    area_nm = citydata.get("AREA_NM")
    area_cd = citydata.get("AREA_CD")
    area_uri = EX["Area_" + normalize_id(area_cd or area_nm)]
    graph.add((area_uri, RDF.type, EX.Area))
    if area_nm:
        graph.add((area_uri, RDFS.label, Literal(area_nm)))
    if area_cd:
        graph.add((area_uri, EX.areaCode, Literal(area_cd)))
    return area_uri


def map_live_population(graph: Graph, area_uri: URIRef, live_ppltn_list):
    if not live_ppltn_list:
        return
    for item in live_ppltn_list:
        ts = item.get("PPLTN_TIME")
        pop_uri = EX["PopulationStatus_" + normalize_id(item.get("AREA_CD", "")) + "_" + normalize_id(ts or "now")]
        graph.add((pop_uri, RDF.type, EX.PopulationStatus))
        graph.add((area_uri, EX.hasPopulationStatus, pop_uri))

        # 주요 속성
        if item.get("AREA_CONGEST_LVL"):
            graph.add((pop_uri, EX.congestionLevel, Literal(item["AREA_CONGEST_LVL"])))
        if item.get("AREA_CONGEST_MSG"):
            graph.add((pop_uri, EX.congestionMessage, Literal(item["AREA_CONGEST_MSG"])))
        for k in [
            "AREA_PPLTN_MIN", "AREA_PPLTN_MAX",
            "MALE_PPLTN_RATE", "FEMALE_PPLTN_RATE",
            "PPLTN_RATE_0", "PPLTN_RATE_10", "PPLTN_RATE_20",
            "PPLTN_RATE_30", "PPLTN_RATE_40", "PPLTN_RATE_50",
            "PPLTN_RATE_60", "PPLTN_RATE_70",
            "RESNT_PPLTN_RATE", "NON_RESNT_PPLTN_RATE"
        ]:
            if item.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((pop_uri, pred, Literal(item[k])))

        if ts:
            lit = to_xsd_datetime_literal(ts)
            if lit:
                graph.add((pop_uri, EX.observedAt, lit))

        # 예측 인구 FCST_PPLTN
        fcst_list = item.get("FCST_PPLTN") or []
        for fcst in fcst_list:
            ftime = fcst.get("FCST_TIME")
            fc_uri = EX["PopulationForecast_" + normalize_id(item.get("AREA_CD", "")) + "_" + normalize_id(ftime or "")]
            graph.add((fc_uri, RDF.type, EX.PopulationForecast))
            graph.add((pop_uri, EX.hasPopulationForecast, fc_uri))
            if ftime:
                lit = to_xsd_datetime_literal(ftime)
                if lit:
                    graph.add((fc_uri, EX.forecastTime, lit))
            for k in ["FCST_CONGEST_LVL", "FCST_PPLTN_MIN", "FCST_PPLTN_MAX"]:
                if fcst.get(k) not in (None, ""):
                    pred = EX[normalize_id(k.lower())]
                    graph.add((fc_uri, pred, Literal(fcst[k])))


def map_road_traffic(graph: Graph, area_uri: URIRef, road_data: dict):
    if not road_data:
        return

    avg = road_data.get("AVG_ROAD_DATA") or {}
    # AREA 단위 평균 교통 상태
    avg_uri = EX["RoadTrafficSummary_" + normalize_id(graph.value(area_uri, EX.areaCode) or "")]
    graph.add((avg_uri, RDF.type, EX.RoadTrafficSummary))
    graph.add((area_uri, EX.hasRoadTrafficSummary, avg_uri))

    if avg.get("ROAD_MSG"):
        graph.add((avg_uri, EX.roadMessage, Literal(avg["ROAD_MSG"])))
    if avg.get("ROAD_TRAFFIC_IDX"):
        graph.add((avg_uri, EX.roadTrafficIndex, Literal(avg["ROAD_TRAFFIC_IDX"])))
    if avg.get("ROAD_TRAFFIC_SPD") not in (None, ""):
        graph.add((avg_uri, EX.roadTrafficSpeed, Literal(avg["ROAD_TRAFFIC_SPD"], datatype=XSD.decimal)))
    if avg.get("ROAD_TRAFFIC_TIME"):
        lit = to_xsd_datetime_literal(avg["ROAD_TRAFFIC_TIME"])
        if lit:
            graph.add((avg_uri, EX.observedAt, lit))

    # 개별 링크
    links = road_data.get("ROAD_TRAFFIC_STTS") or []
    for item in links:
        link_id = item.get("LINK_ID")
        link_uri = EX["RoadLink_" + normalize_id(link_id)]
        graph.add((link_uri, RDF.type, EX.RoadLink))
        graph.add((area_uri, EX.hasRoadLink, link_uri))

        if item.get("ROAD_NM"):
            graph.add((link_uri, RDFS.label, Literal(item["ROAD_NM"])))
        for k in ["DIST", "SPD", "IDX"]:
            if item.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((link_uri, pred, Literal(item[k])))

        # 시작/끝 노드 좌표를 POINT로
        if item.get("START_ND_XY"):
            try:
                lon, lat = item["START_ND_XY"].split("_", 1)
                start_node = EX["RoadNode_" + normalize_id(item.get("START_ND_CD", ""))]
                graph.add((start_node, RDF.type, EX.RoadNode))
                if item.get("START_ND_NM"):
                    graph.add((start_node, RDFS.label, Literal(item["START_ND_NM"])))
                add_point_geometry(graph, start_node, lon, lat)
                graph.add((link_uri, EX.startNode, start_node))
            except Exception:
                pass

        if item.get("END_ND_XY"):
            try:
                lon, lat = item["END_ND_XY"].split("_", 1)
                end_node = EX["RoadNode_" + normalize_id(item.get("END_ND_CD", ""))]
                graph.add((end_node, RDF.type, EX.RoadNode))
                if item.get("END_ND_NM"):
                    graph.add((end_node, RDFS.label, Literal(item["END_ND_NM"])))
                add_point_geometry(graph, end_node, lon, lat)
                graph.add((link_uri, EX.endNode, end_node))
            except Exception:
                pass

        # XYLIST를 LINESTRING으로
        if item.get("XYLIST"):
            add_linestring_geometry_from_xylist(graph, link_uri, item["XYLIST"])


def map_parking(graph: Graph, area_uri: URIRef, prk_list):
    if not prk_list:
        return
    for item in prk_list:
        prk_cd = item.get("PRK_CD")
        prk_uri = EX["ParkingLot_" + normalize_id(prk_cd or item.get("PRK_NM", ""))]
        graph.add((prk_uri, RDF.type, EX.ParkingLot))
        graph.add((area_uri, EX.hasParkingLot, prk_uri))

        if item.get("PRK_NM"):
            graph.add((prk_uri, RDFS.label, Literal(item["PRK_NM"])))
        if prk_cd:
            graph.add((prk_uri, EX.parkingCode, Literal(prk_cd)))
        for k in ["PRK_TYPE", "CPCTY", "CUR_PRK_CNT", "CUR_PRK_YN", "PAY_YN",
                  "RATES", "TIME_RATES", "ADD_RATES", "ADD_TIME_RATES",
                  "ADDRESS", "ROAD_ADDR"]:
            if item.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((prk_uri, pred, Literal(item[k])))

        # 좌표
        add_point_geometry(graph, prk_uri, item.get("LNG"), item.get("LAT"))


def map_subway(graph: Graph, area_uri: URIRef, sub_stts, live_sub_ppltn):
    # 개별 역
    if sub_stts:
        for st in sub_stts:
            name = st.get("SUB_STN_NM")
            line = st.get("SUB_STN_LINE")
            st_uri = EX["SubwayStation_" + normalize_id(name or "") + "_" + normalize_id(line or "")]
            graph.add((st_uri, RDF.type, EX.SubwayStation))
            graph.add((area_uri, EX.hasSubwayStation, st_uri))
            if name:
                graph.add((st_uri, RDFS.label, Literal(name)))
            if line:
                graph.add((st_uri, EX.subwayLine, Literal(line)))
            if st.get("SUB_STN_RADDR"):
                graph.add((st_uri, EX.roadAddress, Literal(st["SUB_STN_RADDR"])))
            # 좌표
            add_point_geometry(graph, st_uri, st.get("SUB_STN_X"), st.get("SUB_STN_Y"))

    # 집계 승하차 인원
    if live_sub_ppltn:
        time_str = live_sub_ppltn.get("SUB_STN_TIME")
        sp_uri = EX["SubwayPopulationStatus_" + normalize_id(time_str or "")]
        graph.add((sp_uri, RDF.type, EX.SubwayPopulationStatus))
        graph.add((area_uri, EX.hasSubwayPopulationStatus, sp_uri))
        if time_str:
            lit = to_xsd_datetime_literal(time_str)
            if lit:
                graph.add((sp_uri, EX.observedAt, lit))
        for k, v in live_sub_ppltn.items():
            if k == "SUB_STN_TIME":
                continue
            if v not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((sp_uri, pred, Literal(v)))


def map_bus(graph: Graph, area_uri: URIRef, bus_stn_stts, live_bus_ppltn):
    # 개별 정류장
    if bus_stn_stts:
        for st in bus_stn_stts:
            st_id = st.get("BUS_STN_ID")
            st_uri = EX["BusStop_" + normalize_id(st_id or st.get("BUS_ARS_ID", ""))]
            graph.add((st_uri, RDF.type, EX.BusStop))
            graph.add((area_uri, EX.hasBusStop, st_uri))
            if st.get("BUS_STN_NM"):
                graph.add((st_uri, RDFS.label, Literal(st["BUS_STN_NM"])))
            for k in ["BUS_STN_ID", "BUS_ARS_ID"]:
                if st.get(k) not in (None, ""):
                    pred = EX[normalize_id(k.lower())]
                    graph.add((st_uri, pred, Literal(st[k])))
            add_point_geometry(graph, st_uri, st.get("BUS_STN_X"), st.get("BUS_STN_Y"))

    # 집계 승하차 인원
    if live_bus_ppltn:
        time_str = live_bus_ppltn.get("BUS_STN_TIME")
        bp_uri = EX["BusPopulationStatus_" + normalize_id(time_str or "")]
        graph.add((bp_uri, RDF.type, EX.BusPopulationStatus))
        graph.add((area_uri, EX.hasBusPopulationStatus, bp_uri))
        if time_str:
            lit = to_xsd_datetime_literal(time_str)
            if lit:
                graph.add((bp_uri, EX.observedAt, lit))
        for k, v in live_bus_ppltn.items():
            if k == "BUS_STN_TIME":
                continue
            if v not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((bp_uri, pred, Literal(v)))


def map_bike(graph: Graph, area_uri: URIRef, sbike_stts):
    if not sbike_stts:
        return
    for item in sbike_stts:
        spot_id = item.get("SBIKE_SPOT_ID")
        spot_uri = EX["BikeStation_" + normalize_id(spot_id or "")]
        graph.add((spot_uri, RDF.type, EX.BikeStation))
        graph.add((area_uri, EX.hasBikeStation, spot_uri))
        if item.get("SBIKE_SPOT_NM"):
            graph.add((spot_uri, RDFS.label, Literal(item["SBIKE_SPOT_NM"])))
        for k in ["SBIKE_SHARED", "SBIKE_PARKING_CNT", "SBIKE_RACK_CNT"]:
            if item.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((spot_uri, pred, Literal(item[k])))
        add_point_geometry(graph, spot_uri, item.get("SBIKE_X"), item.get("SBIKE_Y"))


def map_weather(graph: Graph, area_uri: URIRef, weather_stts):
    if not weather_stts:
        return
    for item in weather_stts:
        wtime = item.get("WEATHER_TIME")
        w_uri = EX["WeatherStatus_" + normalize_id(wtime or "")]
        graph.add((w_uri, RDF.type, EX.WeatherStatus))
        graph.add((area_uri, EX.hasWeatherStatus, w_uri))
        if wtime:
            lit = to_xsd_datetime_literal(wtime)
            if lit:
                graph.add((w_uri, EX.observedAt, lit))

        for k in [
            "TEMP", "SENSIBLE_TEMP", "MAX_TEMP", "MIN_TEMP", "HUMIDITY",
            "WIND_DIRCT", "WIND_SPD", "PRECIPITATION", "PRECPT_TYPE",
            "PCP_MSG", "SUNRISE", "SUNSET",
            "UV_INDEX_LVL", "UV_INDEX", "UV_MSG",
            "PM25_INDEX", "PM25", "PM10_INDEX", "PM10",
            "AIR_IDX", "AIR_IDX_MVL", "AIR_IDX_MAIN", "AIR_MSG"
        ]:
            if item.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((w_uri, pred, Literal(item[k])))

        # 24시간 예보
        fc_list = item.get("FCST24HOURS") or []
        for fc in fc_list:
            fdt = fc.get("FCST_DT")
            fc_uri = EX["WeatherForecast_" + normalize_id(fdt or "")]
            graph.add((fc_uri, RDF.type, EX.WeatherForecast))
            graph.add((w_uri, EX.hasWeatherForecast, fc_uri))
            if fdt:
                lit = to_xsd_datetime_literal(fdt)
                if lit:
                    graph.add((fc_uri, EX.forecastTime, lit))
            for k in ["TEMP", "PRECIPITATION", "PRECPT_TYPE", "RAIN_CHANCE", "SKY_STTS"]:
                if fc.get(k) not in (None, ""):
                    pred = EX[normalize_id(k.lower())]
                    graph.add((fc_uri, pred, Literal(fc[k])))


def map_ev_chargers(graph: Graph, area_uri: URIRef, charger_stts):
    if not charger_stts:
        return
    for stat in charger_stts:
        stat_id = stat.get("STAT_ID")
        stat_uri = EX["EVChargerStation_" + normalize_id(stat_id or stat.get("STAT_NM", ""))]
        graph.add((stat_uri, RDF.type, EX.EVChargerStation))
        graph.add((area_uri, EX.hasEVChargerStation, stat_uri))

        if stat.get("STAT_NM"):
            graph.add((stat_uri, RDFS.label, Literal(stat["STAT_NM"].strip())))
        for k in ["STAT_ADDR", "STAT_USETIME", "STAT_PARKPAY", "STAT_LIMITYN",
                  "STAT_LIMITDETAIL", "STAT_KINDDETAIL"]:
            if stat.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((stat_uri, pred, Literal(stat[k])))

        add_point_geometry(graph, stat_uri, stat.get("STAT_X"), stat.get("STAT_Y"))

        details = stat.get("CHARGER_DETAILS") or []
        for ch in details:
            ch_id = ch.get("CHARGER_ID")
            ch_uri = EX["EVCharger_" + normalize_id(stat_id or "") + "_" + normalize_id(ch_id or "")]
            graph.add((ch_uri, RDF.type, EX.EVCharger))
            graph.add((stat_uri, EX.hasEVCharger, ch_uri))

            for k in ["CHARGER_TYPE", "CHARGER_STAT", "OUTPUT", "METHOD"]:
                if ch.get(k) not in (None, ""):
                    pred = EX[normalize_id(k.lower())]
                    graph.add((ch_uri, pred, Literal(ch[k])))

            # 시간 관련 필드
            for tk in ["STATUPDDT", "LASTTSDT", "LASTTEDT", "NOWTSDT"]:
                v = ch.get(tk)
                if v not in (None, ""):
                    pred = EX[normalize_id(tk.lower())]
                    lit = to_xsd_datetime_literal(v)
                    if lit:
                        graph.add((ch_uri, pred, lit))


def map_commercial(graph: Graph, area_uri: URIRef, cmrcl: dict):
    if not cmrcl:
        return
    time_str = cmrcl.get("CMRCL_TIME")
    c_uri = EX["CommercialStatus_" + normalize_id(time_str or "")]
    graph.add((c_uri, RDF.type, EX.CommercialStatus))
    graph.add((area_uri, EX.hasCommercialStatus, c_uri))

    if time_str:
        lit = to_xsd_datetime_literal(time_str)
        if lit:
            graph.add((c_uri, EX.observedAt, lit))

    for k in [
        "AREA_CMRCL_LVL", "AREA_SH_PAYMENT_CNT",
        "AREA_SH_PAYMENT_AMT_MIN", "AREA_SH_PAYMENT_AMT_MAX",
        "CMRCL_MALE_RATE", "CMRCL_FEMALE_RATE",
        "CMRCL_10_RATE", "CMRCL_20_RATE", "CMRCL_30_RATE",
        "CMRCL_40_RATE", "CMRCL_50_RATE", "CMRCL_60_RATE",
        "CMRCL_PERSONAL_RATE", "CMRCL_CORPORATION_RATE"
    ]:
        if cmrcl.get(k) not in (None, ""):
            pred = EX[normalize_id(k.lower())]
            graph.add((c_uri, pred, Literal(cmrcl[k])))

    # 업종별 세부
    rsb_list = cmrcl.get("CMRCL_RSB") or []
    for r in rsb_list:
        r_uri = EX["CommercialSectorStatus_" +
                   normalize_id(r.get("RSB_LRG_CTGR", "")) + "_" +
                   normalize_id(r.get("RSB_MID_CTGR", ""))]
        graph.add((r_uri, RDF.type, EX.CommercialSectorStatus))
        graph.add((c_uri, EX.hasSectorStatus, r_uri))
        for k in [
            "RSB_LRG_CTGR", "RSB_MID_CTGR", "RSB_PAYMENT_LVL",
            "RSB_SH_PAYMENT_CNT", "RSB_SH_PAYMENT_AMT_MIN",
            "RSB_SH_PAYMENT_AMT_MAX", "RSB_MCT_CNT", "RSB_MCT_TIME"
        ]:
            if r.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                graph.add((r_uri, pred, Literal(r[k])))


def map_messages(graph: Graph, area_uri: URIRef, dst_list):
    if not dst_list:
        return
    for m in dst_list:
        msg_uri = EX["DisturbanceMessage_" + normalize_id(m.get("CRT_DT", ""))]
        graph.add((msg_uri, RDF.type, EX.DisturbanceMessage))
        graph.add((area_uri, EX.hasDisturbanceMessage, msg_uri))
        for k in ["DST_SE_NM", "EMRG_STEP_NM", "MSG_CN", "CRT_DT"]:
            if m.get(k) not in (None, ""):
                pred = EX[normalize_id(k.lower())]
                if k == "CRT_DT":
                    lit = to_xsd_datetime_literal(m[k])
                    if lit:
                        graph.add((msg_uri, pred, lit))
                else:
                    graph.add((msg_uri, pred, Literal(m[k])))


# ------------------------------
# 메인 변환 함수
# ------------------------------

def citydata_json_to_graph(data: dict) -> Graph:
    g = Graph()
    g.bind("ex", EX)
    g.bind("geo", GEO)

    citydata = data.get("CITYDATA") or {}
    area_uri = map_area(g, citydata)

    # 도메인별 매핑
    map_live_population(g, area_uri, citydata.get("LIVE_PPLTN_STTS"))
    map_road_traffic(g, area_uri, citydata.get("ROAD_TRAFFIC_STTS"))
    map_parking(g, area_uri, citydata.get("PRK_STTS"))
    map_subway(g, area_uri, citydata.get("SUB_STTS"), citydata.get("LIVE_SUB_PPLTN"))
    map_bus(g, area_uri, citydata.get("BUS_STN_STTS"), citydata.get("LIVE_BUS_PPLTN"))
    map_bike(g, area_uri, citydata.get("SBIKE_STTS"))
    map_weather(g, area_uri, citydata.get("WEATHER_STTS"))
    map_ev_chargers(g, area_uri, citydata.get("CHARGER_STTS"))
    map_commercial(g, area_uri, citydata.get("LIVE_CMRCL_STTS"))
    map_messages(g, area_uri, citydata.get("LIVE_DST_MESSAGE"))

    # 전체 JSON을 통째로 generic 매핑해서
    # 놓치는 키 없이 모두 RDF에 보존
    root_obs = EX["CityDataObservation_" + normalize_id(citydata.get("AREA_CD", ""))]
    g.add((root_obs, RDF.type, EX.CityDataObservation))
    g.add((root_obs, EX.observedArea, area_uri))
    # list_total_count, RESULT 등도 포함
    add_all_json_as_generic(g, root_obs, data, prefix="json")

    return g


# ------------------------------
# API 호출 및 출력/업로드
# ------------------------------

def fetch_citydata(area_name: str) -> dict:
    api_key = os.environ.get("SEOUL_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 SEOUL_API_KEY 가 설정되어 있지 않습니다.")
    encoded_area = quote(area_name, safe="")
    url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/citydata/1/6/{encoded_area}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def upload_to_fuseki(ttl: str, endpoint: str, graph_uri: str = None):
    params = {}
    if graph_uri:
        params["graph"] = graph_uri
    headers = {"Content-Type": "text/turtle; charset=utf-8"}
    r = requests.post(endpoint, params=params, data=ttl.encode("utf-8"), headers=headers, timeout=10)
    r.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description="서울 citydata JSON → 온톨로지 변환")
    parser.add_argument("--area", required=True, help="AREA_NM (예: 강남역)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output-file", help="변환된 TTL을 저장할 파일 경로")
    group.add_argument("--fuseki-endpoint", help="Fuseki Graph Store endpoint URL (예: http://localhost:3030/seoul/data)")
    parser.add_argument("--graph-uri", help="Fuseki에 업로드할 그래프 URI (옵션)")
    args = parser.parse_args()

    data = fetch_citydata(args.area)
    g = citydata_json_to_graph(data)
    ttl = g.serialize(format="turtle")

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(ttl)
    elif args.fuseki_endpoint:
        upload_to_fuseki(ttl, args.fuseki_endpoint, graph_uri=args.graph_uri)


if __name__ == "__main__":
    main()
