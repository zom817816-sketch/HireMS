"""
API 路由
"""
import asyncio
import uuid
import json
from datetime import datetime
from typing import Any, Dict
import os
import tempfile

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.models import (
    UploadResumeResponse, QueryRequest, QueryResponse, ScreeningResult, BitableExportRequest
)
from app.core.cache_manager import CacheManager
from app.core.document_parser import DocumentParser
from app.core.extractor import MetadataExtractor
from app.core.llm_client import LLMClient
from app.core.query_parser import QueryParser
from app.core.vector_store_factory import get_vector_store_manager
from app.core.retriever import Retriever
from app.core.filter import HardFilter
from app.core.scorer import Scorer
from app.core.ranker import Ranker
from app.core.analyzer import CandidateAnalyzer
from app.core.result_formatter import ResultFormatter
from app.models.metadata import ResumeMetadata, QueryMetadata
from app.services.email_intake import ImapResumeIntake
from app.services.feishu_bitable import FeishuBitableWriter
from app.services.intake_store import IntakeStore
from config.config import settings

router = APIRouter(prefix="/api/v1")

# 常量
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# 初始化核心组件
llm_client = LLMClient()
cache_manager = CacheManager()
document_parser = DocumentParser(cache_manager=cache_manager)
metadata_extractor = MetadataExtractor(llm_client, cache_manager=cache_manager)
query_parser = QueryParser(llm_client)
vector_store_manager = get_vector_store_manager()
retriever = Retriever(vector_store_manager)
hard_filter = HardFilter()
scorer = Scorer()
ranker = Ranker()
candidate_analyzer = CandidateAnalyzer(llm_client)
result_formatter = ResultFormatter()

# 存储简历和查询结果的内存字典（在实际应用中应使用数据库）
resume_storage: Dict[str, Any] = {}
query_storage: Dict[str, Any] = {}
ops_store = IntakeStore()
mail_intake = ImapResumeIntake(ops_store)
bitable_writer = FeishuBitableWriter()


def _extract_resume_text(filename: str, content: bytes) -> str:
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise ValueError("仅支持 PDF、DOCX、TXT、MD 格式的简历附件")
    if suffix in {".pdf", ".docx"}:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            return document_parser.parse_pdf(tmp_path) if suffix == ".pdf" else document_parser.parse_docx(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    return content.decode("utf-8-sig")


def _ingest_resume(filename: str, content: bytes, source: dict | None = None) -> str:
    """Shared ingestion path for Web uploads and email attachments."""
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"文件大小超过限制 {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    resume_text = _extract_resume_text(filename, content)
    if not resume_text.strip():
        raise ValueError("未能从简历中提取文本")
    metadata = metadata_extractor.extract_metadata(resume_text)
    resume_id = str(uuid.uuid4())
    resume_storage[resume_id] = {
        "id": resume_id, "filename": filename, "text": resume_text,
        "metadata": metadata.dict(), "created_at": datetime.now(), "source": source or {"source": "web"},
    }
    retriever.add_resume(resume_id, resume_text, metadata.dict())
    return resume_id


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    """安全解析 JSON 字符串；若已是目标类型则直接返回。"""
    if default is None:
        default = []
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"JSON parse failed for: {value}")
        return default


def _calc_skill_scores(resume_skills: list, query_metadata: QueryMetadata, overall_skill_score: float) -> list:
    """根据查询要求计算每个技能的单项得分。"""
    if not resume_skills:
        return []

    required = [s.lower() for s in query_metadata.required_skills]
    preferred = [s.lower() for s in query_metadata.preferred_skills]

    scores = []
    for skill in resume_skills:
        sl = str(skill).lower()
        matched = False
        for q in required + preferred:
            if q in sl or sl in q:
                matched = True
                break
        scores.append({
            "name": skill,
            "score": 1.0 if matched else max(overall_skill_score - 0.3, 0.0)
        })
    return scores


@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok"}


@router.get("/resumes")
async def list_resumes():
    """列出已上传的简历（摘要信息）。"""
    items = []
    for rid, data in resume_storage.items():
        meta = data.get("metadata", {}) or {}
        items.append({
            "resume_id": rid,
            "filename": data.get("filename", ""),
            "name": meta.get("name", ""),
            "created_at": data.get("created_at"),
        })
    return {"total": len(items), "resumes": items}


@router.post("/resumes", response_model=UploadResumeResponse)
async def upload_resume(file: UploadFile = File(...)):
    """
    上传简历接口
    """
    logger.info(f"[upload_resume] 开始处理文件: {file.filename}")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    try:
        content = await file.read()
        resume_id = await run_in_threadpool(_ingest_resume, file.filename, content, {"source": "web"})

        return UploadResumeResponse(
            resume_id=resume_id,
            message=f"简历 '{file.filename}' 上传成功"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("上传简历失败")
        raise HTTPException(status_code=500, detail="上传简历失败，请稍后重试")


@router.get("/operations/status")
async def operations_status():
    """Expose setup state without ever returning a secret or password."""
    return {
        "mail": {"configured": mail_intake.configured(), "host": settings.MAIL_IMAP_HOST, "user": settings.MAIL_IMAP_USER},
        "bitable": {"configured": bitable_writer.configured(), "app_token": settings.FEISHU_BITABLE_APP_TOKEN[-6:] if settings.FEISHU_BITABLE_APP_TOKEN else ""},
        "logs": ops_store.recent_logs(),
    }


@router.post("/operations/mail-sync")
async def sync_mailbox():
    try:
        return await run_in_threadpool(mail_intake.fetch, _ingest_resume)
    except ValueError as e:
        ops_store.log("mail_sync", "blocked", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("邮箱抓取失败")
        ops_store.log("mail_sync", "failed", str(e))
        raise HTTPException(status_code=502, detail=f"邮箱抓取失败：{e}")


@router.post("/queries", response_model=QueryResponse)
async def submit_query(query_request: QueryRequest):
    """
    提交筛选查询接口
    """
    try:
        query_metadata = await run_in_threadpool(query_parser.parse_query, query_request.query_text)
        query_id = str(uuid.uuid4())

        query_storage[query_id] = {
            "id": query_id,
            "text": query_request.query_text,
            "metadata": query_metadata.dict(),
            "created_at": datetime.now()
        }

        return QueryResponse(
            query_id=query_id,
            message="查询提交成功"
        )

    except Exception as e:
        logger.exception("提交查询失败")
        raise HTTPException(status_code=500, detail="提交查询失败，请稍后重试")


@router.get("/results/{query_id}", response_model=ScreeningResult)
async def get_screening_results(query_id: str):
    """
    获取筛选结果接口
    """
    if query_id not in query_storage:
        raise HTTPException(status_code=404, detail="查询不存在")

    try:
        query_data = query_storage[query_id]
        query_metadata = QueryMetadata(**query_data["metadata"])

        retrieved_resumes = await run_in_threadpool(retriever.retrieve, query_metadata)
        filtered_resumes = await run_in_threadpool(hard_filter.filter_resumes, retrieved_resumes, query_metadata)
        scored_resumes = await run_in_threadpool(scorer.score_resumes, filtered_resumes, query_metadata)
        ranked_resumes = await run_in_threadpool(ranker.rank_resumes, scored_resumes, query_metadata)
        analyzed_candidates = await run_in_threadpool(candidate_analyzer.analyze_candidates, ranked_resumes, query_metadata)
        formatted_results = await run_in_threadpool(result_formatter.format_results, analyzed_candidates, query_metadata)

        candidates = []
        for candidate_data in formatted_results["candidates"]:
            basic_info = candidate_data.get("basic_info", {}) or {}
            scores = candidate_data.get("scores", {}) or {}
            overall_skill_score = scores.get("skill_score", 0)

            resume_skills = _safe_json_loads(basic_info.get("skills", []), [])
            skill_scores = _calc_skill_scores(resume_skills, query_metadata, overall_skill_score)

            work_experience = _safe_json_loads(basic_info.get("work_experience", []), [])
            education = _safe_json_loads(basic_info.get("education", []), [])

            candidate = {
                "id": candidate_data.get("id", ""),
                "rank": candidate_data.get("rank", 0),
                "name": candidate_data.get("name", ""),
                "email": candidate_data.get("contact_info", {}).get("email"),
                "phone": candidate_data.get("contact_info", {}).get("phone"),
                "overall_score": scores.get("overall_score", 0),
                "work_experience": work_experience,
                "education": education,
                "skill_scores": skill_scores,
                "skills": resume_skills,
                "expected_salary": basic_info.get("expected_salary"),
                "preferred_locations": basic_info.get("preferred_locations", []),
                "analysis": candidate_data.get("analysis", "")
            }
            candidates.append(candidate)

        return ScreeningResult(
            query_id=query_id,
            query_text=query_data["text"],
            total_candidates=formatted_results["total_candidates"],
            candidates=candidates,
            created_at=query_data["created_at"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取筛选结果失败")
        raise HTTPException(status_code=500, detail="获取筛选结果失败，请稍后重试")


@router.post("/operations/bitable-export")
async def export_to_bitable(request: BitableExportRequest):
    """Run the selected screening query and export qualifying candidates."""
    try:
        screening = await get_screening_results(request.query_id)
        candidates = [candidate.dict() for candidate in screening.candidates]
        count = await run_in_threadpool(bitable_writer.write_candidates, candidates, request.job_name)
        ops_store.log("bitable_export", "success", f"岗位 {request.job_name} 写入 {count} 位候选人")
        return {"exported": count, "message": f"已写入 {count} 条候选人记录"}
    except HTTPException:
        raise
    except ValueError as e:
        ops_store.log("bitable_export", "blocked", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("多维表格写入失败")
        ops_store.log("bitable_export", "failed", str(e))
        raise HTTPException(status_code=502, detail=f"多维表格写入失败：{e}")


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: str):
    """
    获取简历详情接口
    """
    if resume_id not in resume_storage:
        raise HTTPException(status_code=404, detail="简历不存在")

    try:
        return resume_storage[resume_id]
    except Exception:
        logger.exception("获取简历详情失败")
        raise HTTPException(status_code=500, detail="获取简历详情失败，请稍后重试")
