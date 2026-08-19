from app.core.normalization import normalize_query, normalize_resume_metadata
from app.core.filter import HardFilter
from app.models.metadata import QueryMetadata


def test_normalizes_shanghai_education_and_course_sales_tags():
    data = normalize_resume_metadata({"address": "上海市浦东新区", "education": [{"degree": "全日制大专"}], "work_experience": [{"title": "课程顾问", "description": "负责K12学员试听转化和续费"}]})
    assert data["location_city"] == ["上海"]
    assert data["location_codes"] == ["310000"]
    assert data["highest_degree_level"] == 1 and data["full_time_education"] is True
    assert data["role_tags"] == ["education_course_sales"]
    assert data["industry_tags"] == ["k12_academic_education"]


def test_course_sales_query_uses_tags_not_literal_skill_filter():
    query = QueryMetadata(**normalize_query({"required_skills": ["课程销售", "K12文化课销售"], "required_education": "全日制大专及以上", "locations": ["上海"], "custom_conditions": ""}))
    assert query.required_skills == []
    candidate = {"id": "1", "metadata": normalize_resume_metadata({"address": "浦东新区", "education": [{"degree": "全日制大专"}], "work_experience": [{"title": "课程顾问", "description": "K12学员课程续费转化"}]})}
    assert HardFilter().filter_resumes([candidate], query) == [candidate]


def test_age_is_normalized_and_applied_as_a_hard_filter():
    query = QueryMetadata(**normalize_query({"custom_conditions": "\u5e74\u9f8435\u5c81\u4ee5\u5185"}))
    assert query.max_age == 35
    candidate = {"id": "over", "text": "\u5e74\u9f8436\u5c81", "metadata": normalize_resume_metadata({}, source_text="\u5e74\u9f8436\u5c81")}
    assert HardFilter().filter_resumes([candidate], query) == []
