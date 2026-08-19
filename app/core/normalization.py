"""Deterministic normalization for fields used by recruitment rules."""
from __future__ import annotations
import re
from typing import Any

CITY_RULES = {
    "上海": ("上海", "310000", ("上海", "上海市", "浦东", "闵行", "徐汇", "静安", "黄浦", "杨浦", "宝山", "嘉定", "松江", "青浦", "奉贤", "金山", "崇明")),
    "北京": ("北京", "110000", ("北京", "北京市", "朝阳", "海淀", "通州", "丰台", "大兴", "昌平", "顺义")),
    "广州": ("广州", "440100", ("广州", "广州市", "天河", "越秀", "海珠", "白云", "番禺")),
    "深圳": ("深圳", "440300", ("深圳", "深圳市", "南山", "福田", "宝安", "龙岗", "龙华")),
}
DEGREE_RULES = (("博士", 4, "doctor"), ("硕士", 3, "master"), ("研究生", 3, "master"), ("本科", 2, "bachelor"), ("学士", 2, "bachelor"), ("大专", 1, "college"), ("专科", 1, "college"))
TAG_RULES = {
    "education_course_sales": ("课程销售", "课程顾问", "招生顾问", "教育销售", "试听转化", "招生转化", "续费转化", "促单"),
    "k12_academic_education": ("k12", "学科培训", "文化课", "语文", "数学", "英语", "小初高"),
}

def normalize_locations(values: list[Any], address: str = "") -> tuple[list[str], list[str]]:
    text = " ".join([*(str(value) for value in (values or [])), address or ""]).lower()
    cities, codes = [], []
    for city, (_, code, aliases) in CITY_RULES.items():
        if any(alias.lower() in text for alias in aliases):
            cities.append(city); codes.append(code)
    return cities, codes

def normalize_degree(value: Any) -> tuple[int, str | None]:
    text = str(value or "")
    for marker, level, tag in DEGREE_RULES:
        if marker in text: return level, tag
    return 0, None

def normalize_resume_metadata(data: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    data = dict(data)
    education = data.get("education") or []
    highest, full_time = 0, None
    for item in education:
        if not isinstance(item, dict): continue
        level, tag = normalize_degree(item.get("degree"))
        item["degree_level"], item["degree_tag"] = level, tag
        raw = " ".join(map(str, item.values())).lower()
        item["is_full_time"] = False if any(x in raw for x in ("非全", "成人", "自考", "开放大学", "函授", "网络教育")) else (True if any(x in raw for x in ("全日制", "统招", "普通高等")) else None)
        highest = max(highest, level)
        if item["is_full_time"] is True: full_time = True
        elif full_time is None and item["is_full_time"] is False: full_time = False
    raw_locations = data.get("preferred_locations") or []
    cities, codes = normalize_locations(raw_locations, data.get("address") or "")
    text = " ".join([str(data.get("summary", "")), str(data.get("additional_info", "")), str(data.get("work_experience", "")), str(data.get("skills", "")), source_text]).lower()
    data.update({"education": education, "location_raw": raw_locations, "location_city": cities, "location_codes": codes, "highest_degree_level": highest, "full_time_education": full_time,
                 "role_tags": [tag for tag, aliases in TAG_RULES.items() if tag == "education_course_sales" and any(a.lower() in text for a in aliases)],
                 "industry_tags": [tag for tag, aliases in TAG_RULES.items() if tag == "k12_academic_education" and any(a.lower() in text for a in aliases)]})
    age_match = re.search(r"(?<!\d)([1-9]\d)\s*岁", text)
    data["age"] = int(age_match.group(1)) if age_match else data.get("age")
    return data

def normalize_query(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    required_skills = data.get("required_skills") or []
    required_industries = data.get("required_industries") or []
    raw = " ".join(map(str, required_skills + required_industries + [data.get("required_education", ""), data.get("custom_conditions", "")])).lower()
    data["required_role_tags"] = ["education_course_sales"] if any(a.lower() in raw for a in TAG_RULES["education_course_sales"]) else []
    data["required_industry_tags"] = ["k12_academic_education"] if any(a.lower() in raw for a in TAG_RULES["k12_academic_education"]) else []
    if "全日制" in raw: data["full_time_education"] = True
    age_match = re.search(r"(?:年龄|年纪).{0,12}?([1-9]\d)\s*岁", raw)
    data["max_age"] = int(age_match.group(1)) if age_match else None
    level, tag = normalize_degree(data.get("required_education")); data["required_education"] = {1:"大专",2:"本科",3:"硕士",4:"博士"}.get(level, data.get("required_education"))
    # Role/industry phrases must not become literal skill hard-filters.
    aliases = {a.lower() for values in TAG_RULES.values() for a in values}
    data["required_skills"] = [s for s in required_skills if not any(alias in str(s).lower() for alias in aliases)]
    return data
