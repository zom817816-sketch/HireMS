from app.core.normalization import normalize_job_category, normalize_query, normalize_resume_metadata
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


def test_education_job_categories_are_broad_and_stable():
    assert normalize_job_category(source_text="曾任K12课程顾问，负责邀约和签单") == "销售"
    assert normalize_job_category(source_text="初中数学老师，负责备课和授课") == "教师"
    assert normalize_job_category(source_text="校区行政和财务支持") == "职能"


def test_query_category_uses_keywords_and_is_not_over_specific():
    sales = normalize_query({"keywords": ["少儿英语课程销售顾问"]})
    teacher = normalize_query({"keywords": ["高中数学教师"]})
    assert sales["job_category"] == "销售"
    assert teacher["job_category"] == "教师"


def test_description_sales_terms_do_not_override_a_structured_design_title():
    data = normalize_resume_metadata({
        "work_experience": [{
            "title": "高级交互设计师",
            "description": "优化咨询流程并提升落地页销售转化率",
        }]
    })
    assert data["job_category"] == "产品技术"


def test_resume_target_role_outranks_older_work_title():
    data = normalize_resume_metadata(
        {"work_experience": [{"title": "少儿口才老师"}]},
        source_text="王晓玉｜求职方向：销售专员｜期望城市：上海\n工作经历：少儿口才老师",
    )
    assert data["job_category"] == "销售"


def test_extended_education_company_categories():
    cases = {
        "学管师，负责家校服务和学员管理": "教务学管",
        "校区运营，负责社群和活动执行": "运营",
        "品牌市场经理，负责新媒体投放": "市场",
        "校区校长，负责区域经营": "管理",
        "高级后端工程师": "产品技术",
        "招聘主管，负责薪酬绩效": "职能",
    }
    assert {text: normalize_job_category(source_text=text) for text in cases} == cases
