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
JOB_CATEGORIES = (
    "销售", "教师", "教务学管", "运营", "市场", "管理", "产品技术", "职能", "其他",
)
JOB_CATEGORY_VERSION = 2
JOB_CATEGORY_ALIASES = {
    "销售": (
        "销售", "课程顾问", "招生顾问", "教育顾问", "学习规划师", "咨询师",
        "市场顾问", "电销", "邀约", "渠道", "商务拓展", "续费", "转化", "签单",
    ),
    "教师": (
        "教师", "老师", "讲师", "教研", "主讲", "助教", "授课", "教学", "备课",
        "带班", "磨课",
    ),
    "教务学管": (
        "教务", "学管", "学管师", "班主任", "学习管理师", "教学服务", "排课",
        "家校服务", "学员管理",
    ),
    "运营": (
        "运营", "校区运营", "社群运营", "用户运营", "活动运营", "教学运营",
        "社群", "用户增长", "活动执行",
    ),
    "市场": (
        "市场", "市场营销", "市场运营", "品牌", "新媒体", "内容营销", "广告投放",
        "媒介", "公关", "推广", "kol",
    ),
    "管理": (
        "校长", "校区负责人", "区域负责人", "城市负责人", "事业部负责人",
        "总经理", "总监",
    ),
    "产品技术": (
        "产品经理", "产品设计", "开发", "工程师", "程序员", "架构师", "设计师",
        "ui", "ux", "测试", "运维", "算法", "数据工程",
    ),
    "职能": (
        "人事", "招聘", "hrbp", "行政", "财务", "会计", "出纳", "法务", "采购",
        "审计", "薪酬绩效",
    ),
}


def _category_from_text(value: Any) -> str:
    text = str(value or "").lower()
    aliases = dict(JOB_CATEGORY_ALIASES)
    aliases["销售"] = aliases["销售"] + ("sales", "business development")
    aliases["教师"] = aliases["教师"] + ("teacher", "tutor", "lecturer")
    scores = {
        category: sum(1 for alias in values if alias.lower() in text)
        for category, values in aliases.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "其他"


def normalize_job_category(
    value: Any = None, source_text: str = "", work_experience: list[Any] | None = None,
) -> str:
    """Map education-company roles to a deliberately broad stable taxonomy."""
    explicit = _category_from_text(value)
    if explicit != "其他":
        return explicit
    # Resume headers usually contain the current target role and should outrank
    # older work history (for example, a teacher currently applying for sales).
    headline = _category_from_text(str(source_text or "")[:120])
    if headline != "其他":
        return headline
    has_structured_title = False
    for item in work_experience or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        has_structured_title = has_structured_title or bool(str(title).strip())
        category = _category_from_text(title)
        if category != "其他":
            return category
    # Descriptions often mention "sales support" or "conversion rate" even when
    # the actual role is design/operations; a structured non-matching title wins.
    if has_structured_title:
        return "其他"
    return _category_from_text(source_text)

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
                 "industry_tags": [tag for tag, aliases in TAG_RULES.items() if tag == "k12_academic_education" and any(a.lower() in text for a in aliases)],
                 "job_category": normalize_job_category(data.get("job_category"), source_text, data.get("work_experience") or []),
                 "job_category_version": JOB_CATEGORY_VERSION})
    age_match = re.search(r"(?<!\d)([1-9]\d)\s*岁", text)
    data["age"] = int(age_match.group(1)) if age_match else data.get("age")
    return data

def normalize_query(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    required_skills = data.get("required_skills") or []
    required_industries = data.get("required_industries") or []
    raw = " ".join(map(str, (data.get("keywords") or []) + required_skills + required_industries + [data.get("required_education", ""), data.get("custom_conditions", "")])).lower()
    inferred_category = normalize_job_category(None, raw)
    data["job_category"] = (
        inferred_category if inferred_category != "其他"
        else normalize_job_category(data.get("job_category"), raw)
    )
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
