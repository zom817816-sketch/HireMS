"""
API 路由
"""
import asyncio
import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import os
import tempfile
import threading

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.models import (
    UploadResumeResponse, QueryRequest, QueryResponse, ScreeningResult, BitableExportRequest,
    CandidateActionRequest, InterviewCreateRequest, InterviewFeedbackRequest,
    InterviewRescheduleRequest, InterviewCancelRequest, OfferUpdateRequest,
)
from app.core.cache_manager import CacheManager
from app.core.document_parser import DocumentParser
from app.core.extractor import MetadataExtractor
from app.core.llm_client import LLMClient
from app.core.query_parser import QueryParser
from app.core.normalization import (
    JOB_CATEGORIES, JOB_CATEGORY_VERSION, normalize_resume_metadata,
)
from app.core.vector_store_factory import get_vector_store_manager
from app.core.retriever import Retriever
from app.core.filter import HardFilter
from app.core.scorer import Scorer
from app.core.ranker import Ranker
from app.core.analyzer import CandidateAnalyzer
from app.core.result_formatter import ResultFormatter
from app.core.deduplication import (
    normalize_email, normalize_name, normalize_phone, resume_fingerprint,
)
from app.models.metadata import ResumeMetadata, QueryMetadata
from app.services.email_intake import ImapResumeIntake
from app.services.feishu_bitable import FeishuBitableWriter
from app.services.intake_store import IntakeStore
from app.services.resume_file_store import ResumeFileStore
from app.services.feishu_workflow import FeishuWorkflowClient
from app.services.candidate_email import CandidateEmailNotifier
from app.services.recruitment_state import (
    InvalidTransition, candidate_action_target, expected_next_round, feedback_target,
    offer_target, validate_schedule,
)
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
resume_file_store = ResumeFileStore()
mail_intake = ImapResumeIntake(ops_store)
bitable_writer = FeishuBitableWriter()
feishu_workflow = FeishuWorkflowClient()
candidate_email = CandidateEmailNotifier()
_legacy_recall_migration_lock = threading.Lock()
_legacy_recall_migration_done = False

def _store_resume_original(resume_id: str, filename: str, content: bytes) -> dict:
    """Save an original and atomically switch its database association."""
    previous = ops_store.get_resume_file(resume_id)
    stored = resume_file_store.save(resume_id, filename, content)
    ops_store.record_resume_file(
        resume_id=resume_id,
        original_filename=stored["original_filename"],
        relative_path=stored["relative_path"],
        media_type=stored["media_type"],
        size_bytes=stored["size_bytes"],
    )
    if isinstance(previous, dict) and previous.get("relative_path") != stored["relative_path"]:
        resume_file_store.delete(previous["relative_path"])
    return stored


def _resume_file_available(resume_id: str) -> bool:
    record = ops_store.get_resume_file(resume_id)
    if not isinstance(record, dict):
        return False
    try:
        resume_file_store.resolve(record["relative_path"])
        return True
    except (FileNotFoundError, ValueError, KeyError):
        return False


def _decorate_resume_file(candidate: dict) -> dict:
    return {**candidate, "has_resume_file": _resume_file_available(candidate.get("id", ""))}


def _decorate_workflow_candidate(candidate: dict) -> dict:
    interviews = ops_store.list_interviews(candidate.get("id", ""))
    active = next(
        (item for item in reversed(interviews) if item.get("status") == "已安排"), None,
    )
    pending_feedback = next(
        (item for item in reversed(interviews) if item.get("status") == "待定"), None,
    )
    try:
        next_round = expected_next_round(interviews)
    except InvalidTransition:
        next_round = None
    return {
        **_decorate_resume_file(candidate), "interviews": interviews,
        "active_interview": active, "pending_feedback": pending_feedback,
        "next_round": next_round,
    }


def _upgrade_legacy_recall_metadata() -> int:
    """Backfill category/time on pre-feature vectors without calling the LLM."""
    global _legacy_recall_migration_done
    if _legacy_recall_migration_done:
        return 0
    with _legacy_recall_migration_lock:
        if _legacy_recall_migration_done:
            return 0
        upgraded = 0
        for identity in ops_store.list_resume_identities():
            if (
                identity.get("job_category") in JOB_CATEGORIES
                and identity.get("imported_at_epoch")
                and identity.get("job_category_version", 0) >= JOB_CATEGORY_VERSION
            ):
                continue
            try:
                indexed = retriever.get_indexed_resume(identity["resume_id"])
                if not indexed:
                    logger.warning(f"Legacy resume is missing from vector index: {identity['resume_id']}")
                    continue
                previous_metadata = dict(indexed.get("metadata", {}) or {})
                previous_metadata.pop("job_category", None)
                previous_metadata.pop("job_category_version", None)
                metadata = normalize_resume_metadata(previous_metadata, indexed.get("text", ""))
                raw_time = identity.get("created_at") or identity.get("updated_at")
                imported = datetime.fromisoformat(raw_time) if raw_time else datetime.now().astimezone()
                if imported.tzinfo is None:
                    imported = imported.astimezone()
                imported_utc = imported.astimezone(timezone.utc)
                metadata.update({
                    "imported_at": imported_utc.isoformat(timespec="seconds"),
                    "imported_at_epoch": int(imported_utc.timestamp()),
                })
                retriever.add_resume(identity["resume_id"], indexed.get("text", ""), metadata)
                ops_store.update_resume_recall_metadata(
                    identity["resume_id"], metadata["job_category"],
                    metadata["imported_at"], metadata["imported_at_epoch"],
                    JOB_CATEGORY_VERSION,
                )
                upgraded += 1
            except Exception as error:
                logger.warning(f"Legacy recall metadata upgrade failed for {identity.get('resume_id')}: {error}")
        _legacy_recall_migration_done = True
        if upgraded:
            ops_store.log("resume_metadata_migration", "success", f"已升级 {upgraded} 份历史简历的时间戳和岗位类别")
        return upgraded


WORKFLOW_ACTIONS = {
    "pass": "通过", "reject": "淘汰",
}


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


def _ingest_resume(filename: str, content: bytes, source: dict | None = None) -> dict[str, Any]:
    """Shared ingestion path for Web uploads and email attachments."""
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"文件大小超过限制 {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    resume_text = _extract_resume_text(filename, content)
    if not resume_text.strip():
        raise ValueError("未能从简历中提取文本")

    fingerprint = resume_fingerprint(resume_text)
    exact_match = ops_store.find_resume_by_fingerprint(fingerprint)
    exact_match_is_indexed = bool(
        exact_match
        and exact_match.get("job_category") in JOB_CATEGORIES
        and exact_match.get("imported_at_epoch")
        and exact_match.get("job_category_version", 0) >= JOB_CATEGORY_VERSION
    )
    if exact_match_is_indexed:
        if not ops_store.get_resume_file(exact_match["resume_id"]):
            _store_resume_original(exact_match["resume_id"], filename, content)
        ops_store.log("resume_dedup", "skipped", f"重复简历已跳过：{filename} → {exact_match['resume_id']}")
        return {
            "resume_id": exact_match["resume_id"], "status": "duplicate",
            "name": exact_match.get("name", ""), "possible_duplicate": False,
        }

    metadata = metadata_extractor.extract_metadata(resume_text)
    metadata_dict = metadata.model_dump()
    phone_key = normalize_phone(metadata.phone)
    email_key = normalize_email(metadata.email)
    name_key = normalize_name(metadata.name)
    identity_match = ops_store.find_resume_by_identity(phone_key, email_key)
    resume_id = (
        exact_match["resume_id"] if exact_match
        else identity_match["resume_id"] if identity_match
        else f"resume_{fingerprint[:32]}"
    )
    status = "updated" if exact_match or identity_match else "created"
    possible_duplicate = bool(ops_store.find_resumes_by_name(name_key, exclude_id=resume_id))

    imported_at = datetime.now(timezone.utc)
    metadata_dict.update({
        "content_fingerprint": fingerprint,
        "identity_phone": phone_key,
        "identity_email": email_key,
        "imported_at": imported_at.isoformat(timespec="seconds"),
        "imported_at_epoch": int(imported_at.timestamp()),
    })
    resume_storage[resume_id] = {
        "id": resume_id, "filename": filename, "text": resume_text,
        "metadata": metadata_dict, "created_at": datetime.now(), "source": source or {"source": "web"},
    }
    retriever.add_resume(resume_id, resume_text, metadata_dict)
    ops_store.record_resume_identity(
        resume_id, fingerprint, phone_key, email_key, name_key, metadata.name, filename,
        metadata.job_category, metadata_dict["imported_at"], metadata_dict["imported_at_epoch"],
        JOB_CATEGORY_VERSION,
    )
    _store_resume_original(resume_id, filename, content)
    if possible_duplicate:
        ops_store.log("resume_dedup", "review", f"检测到同名候选人，保留为独立记录：{metadata.name} / {filename}")
    elif status == "updated":
        ops_store.log("resume_dedup", "updated", f"根据手机号/邮箱更新候选人：{metadata.name} / {filename}")
    return {
        "resume_id": resume_id, "status": status, "name": metadata.name,
        "possible_duplicate": possible_duplicate,
    }


def _friendly_ingest_error(error: Exception) -> tuple[int, str]:
    """Return an actionable upload error without leaking credentials or stack traces."""
    message = str(error).lower()
    if any(token in message for token in ("connection error", "connecterror", "timeout", "network")):
        return 502, (
            "无法连接 LLM 服务，简历尚未入库。请检查 HIREMS_LLM_BASE_URL、HIREMS_LLM_API_KEY、"
            "网络/代理设置，以及模型名称是否与方舟接口类型匹配。"
        )
    if any(token in message for token in ("embedding", "chromadb", "vector", "collection")):
        return 503, "向量库写入失败，简历尚未入库。请检查本地 Embedding 模型和 Chroma 数据目录。"
    return 500, "简历解析或结构化提取失败，简历尚未入库。请查看服务端日志获取详细原因。"


def _sync_candidates_to_bitable(
    query_id: str, candidates: list[dict[str, Any]], job_name: str,
) -> dict[str, Any]:
    """Write each high-scoring candidate once for a screening batch."""
    if not bitable_writer.configured():
        raise ValueError("多维表格尚未配置，自动写入已跳过")

    eligible = [
        candidate for candidate in candidates
        if candidate.get("id")
        and float(candidate.get("overall_score", 0)) >= settings.FEISHU_EXPORT_MIN_SCORE
    ]
    synced_ids = ops_store.bitable_synced_candidate_ids(query_id)
    pending = [candidate for candidate in eligible if candidate["id"] not in synced_ids]
    if not pending:
        return {
            "status": "up_to_date" if eligible else "no_eligible_candidates",
            "eligible": len(eligible), "exported": 0,
            "already_synced": len(eligible),
            "min_score": settings.FEISHU_EXPORT_MIN_SCORE,
        }

    record_ids: list[str] = []
    if hasattr(bitable_writer, "create_candidates"):
        record_ids = bitable_writer.create_candidates(pending, job_name)
        exported = len(record_ids)
    else:  # Test doubles and third-party adapters using the original interface.
        exported = bitable_writer.write_candidates(pending, job_name)
    if exported != len(pending):
        raise RuntimeError(f"多维表格返回写入 {exported}/{len(pending)} 条，未记录同步状态")
    ops_store.mark_bitable_synced(
        query_id, [str(candidate["id"]) for candidate in pending], job_name,
        record_ids or None,
    )
    ops_store.log(
        "bitable_export", "success",
        f"岗位 {job_name} 自动写入 {exported} 位高分候选人",
    )
    return {
        "status": "success", "eligible": len(eligible), "exported": exported,
        "already_synced": len(eligible) - exported,
        "min_score": settings.FEISHU_EXPORT_MIN_SCORE,
    }


async def _sync_workflow_to_bitable(
    candidate: dict[str, Any], interview: dict[str, Any] | None = None,
) -> str:
    """Best-effort workflow writeback; local HR actions remain authoritative."""
    record_ids = ops_store.bitable_record_ids(candidate["id"])
    if not isinstance(record_ids, list) or not record_ids:
        return "not_exported"
    fields: dict[str, Any] = {"处理状态": candidate.get("status", "")}
    if candidate.get("status", "").startswith("Offer"):
        fields["Offer状态"] = candidate["status"]
    if interview:
        fields.update({
            "当前面试轮次": interview.get("round_name", ""),
            "面试状态": interview.get("status", ""),
            "面试评价": interview.get("feedback", ""),
            "面试时间": f"{interview.get('start_at', '')} - {interview.get('end_at', '')}",
        })
    try:
        await run_in_threadpool(
            bitable_writer.update_candidate_records, record_ids, fields,
        )
        ops_store.log(
            "bitable_workflow_sync", "success",
            f"{candidate.get('name') or candidate['id']} → {candidate.get('status')}",
        )
        return "synced"
    except Exception as error:
        logger.warning(f"多维表格状态回写失败: {error}")
        ops_store.log("bitable_workflow_sync", "failed", str(error))
        return "failed"


async def _send_candidate_interview_email(
    candidate: dict[str, Any], interview: dict[str, Any], action: str,
) -> str:
    """Best-effort candidate email; never roll back a valid local workflow action."""
    if not candidate.get("email"):
        return "missing_email"
    try:
        if action == "cancelled":
            await run_in_threadpool(candidate_email.interview_cancelled, candidate, interview)
        else:
            label = "改期" if action == "rescheduled" else "安排"
            await run_in_threadpool(
                candidate_email.interview_scheduled, candidate, interview, label,
            )
        ops_store.notification(
            candidate["id"], f"interview_{action}:{interview['interview_id']}",
            "email", "success", candidate.get("email", ""),
        )
        return "sent"
    except ValueError as error:
        ops_store.notification(
            candidate["id"], f"interview_{action}:{interview['interview_id']}",
            "email", "pending", str(error),
        )
        return "not_configured"
    except Exception as error:
        logger.warning(f"候选人面试邮件发送失败: {error}")
        ops_store.notification(
            candidate["id"], f"interview_{action}:{interview['interview_id']}",
            "email", "failed", str(error),
        )
        return "failed"


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
    items_by_id = {item["resume_id"]: item for item in ops_store.list_resume_identities()}
    for rid, data in resume_storage.items():
        meta = data.get("metadata", {}) or {}
        items_by_id[rid] = {
            "resume_id": rid,
            "filename": data.get("filename", ""),
            "name": meta.get("name", ""),
            "created_at": data.get("created_at"),
            "job_category": meta.get("job_category", "其他"),
            "imported_at": meta.get("imported_at", ""),
        }
    items = [
        {**item, "has_resume_file": _resume_file_available(resume_id)}
        for resume_id, item in items_by_id.items()
    ]
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
        result = await run_in_threadpool(_ingest_resume, file.filename, content, {"source": "web"})

        messages = {
            "created": f"简历 '{file.filename}' 已解析并入库",
            "updated": f"检测到相同手机号或邮箱，已更新候选人 '{result.get('name') or file.filename}'",
            "duplicate": f"检测到完全相同的简历，已跳过 '{file.filename}'",
        }
        message = messages[result["status"]]
        if result.get("possible_duplicate"):
            message += "；存在同名候选人，请人工复核"

        return UploadResumeResponse(
            resume_id=result["resume_id"], message=message,
            status=result["status"], possible_duplicate=result.get("possible_duplicate", False),
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("上传简历失败")
        status_code, detail = _friendly_ingest_error(e)
        raise HTTPException(status_code=status_code, detail=detail)


@router.get("/operations/status")
async def operations_status():
    """Expose setup state without ever returning a secret or password."""
    return {
        "mail": {"configured": mail_intake.configured(), "host": settings.MAIL_IMAP_HOST, "user": settings.MAIL_IMAP_USER},
        "candidate_email": {"configured": candidate_email.configured()},
        "bitable": {
            "configured": bitable_writer.configured(),
            "app_token": settings.FEISHU_BITABLE_APP_TOKEN[-6:] if settings.FEISHU_BITABLE_APP_TOKEN else "",
            "auto_export": settings.FEISHU_BITABLE_AUTO_EXPORT,
            "min_score": settings.FEISHU_EXPORT_MIN_SCORE,
        },
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
        cached_result = query_data.get("screening_result")
        if cached_result:
            return ScreeningResult.model_validate(cached_result)
        query_metadata = QueryMetadata(**query_data["metadata"])

        await run_in_threadpool(_upgrade_legacy_recall_metadata)

        retrieved_resumes = await run_in_threadpool(
            retriever.retrieve, query_metadata, settings.SCREENING_RETRIEVAL_LIMIT,
            settings.SCREENING_LOOKBACK_DAYS,
        )
        filtered_resumes = await run_in_threadpool(hard_filter.filter_resumes, retrieved_resumes, query_metadata)
        scored_resumes = await run_in_threadpool(scorer.score_resumes, filtered_resumes, query_metadata)
        ranked_resumes = await run_in_threadpool(ranker.rank_resumes, scored_resumes, query_metadata)
        analyzed_candidates = await run_in_threadpool(
            candidate_analyzer.analyze_candidates,
            ranked_resumes[:settings.SCREENING_ANALYSIS_LIMIT],
            query_metadata,
        )
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
                "analysis": candidate_data.get("analysis", ""),
                "job_category": basic_info.get("job_category", query_metadata.job_category or "其他"),
                "imported_at": basic_info.get("imported_at"),
                "has_resume_file": _resume_file_available(candidate_data.get("id", "")),
            }
            candidates.append(candidate)

        # The result page is also the workflow entry point. Candidate-card
        # automation is intentionally delegated to Feishu Bitable, so HireMS only
        # persists the local review queue here.
        for candidate in candidates:
            ops_store.upsert_candidate(candidate, query_data["text"])

        bitable_sync: dict[str, Any] = {
            "status": "disabled", "eligible": 0, "exported": 0,
            "already_synced": 0, "min_score": settings.FEISHU_EXPORT_MIN_SCORE,
        }
        if settings.FEISHU_BITABLE_AUTO_EXPORT:
            try:
                bitable_sync = await run_in_threadpool(
                    _sync_candidates_to_bitable, query_id, candidates,
                    query_data["text"].strip()[:80] or "未命名岗位",
                )
            except Exception as sync_error:
                logger.warning(f"筛选完成，但自动写入多维表格失败: {sync_error}")
                ops_store.log("bitable_export", "failed", str(sync_error))
                bitable_sync = {
                    "status": "failed", "eligible": sum(
                        1 for candidate in candidates
                        if float(candidate.get("overall_score", 0)) >= settings.FEISHU_EXPORT_MIN_SCORE
                    ),
                    "exported": 0, "already_synced": 0,
                    "min_score": settings.FEISHU_EXPORT_MIN_SCORE,
                    "message": str(sync_error),
                }

        screening_result = ScreeningResult(
            query_id=query_id,
            query_text=query_data["text"],
            total_candidates=formatted_results["total_candidates"],
            candidates=candidates,
            created_at=query_data["created_at"],
            recall_scope={
                "job_category": query_metadata.job_category or "其他",
                "lookback_days": settings.SCREENING_LOOKBACK_DAYS,
            },
            bitable_sync=bitable_sync,
        )
        query_data["screening_result"] = screening_result.model_dump(mode="json")
        return screening_result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取筛选结果失败")
        raise HTTPException(status_code=500, detail="获取筛选结果失败，请稍后重试")


@router.get("/workflow/candidates")
async def list_workflow_candidates(status: str | None = None):
    candidates = [_decorate_workflow_candidate(item) for item in ops_store.list_candidates(status)]
    return {"candidates": candidates, "total": len(candidates)}


@router.get("/workflow/candidates/{candidate_id}")
async def get_workflow_candidate(candidate_id: str):
    candidate = ops_store.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不在工作流队列中")
    decorated = _decorate_workflow_candidate(candidate)
    return {"candidate": decorated, "interviews": decorated["interviews"]}


@router.post("/workflow/candidates/{candidate_id}/action")
async def update_candidate_action(candidate_id: str, request: CandidateActionRequest):
    if request.action not in WORKFLOW_ACTIONS:
        raise HTTPException(status_code=400, detail="不支持的候选人操作")
    try:
        current = ops_store.get_candidate(candidate_id)
        if not current:
            raise KeyError(candidate_id)
        target = candidate_action_target(current["status"], request.action)
        candidate = ops_store.update_candidate(candidate_id, target, request.owner_id)
        ops_store.log("candidate_action", "success", f"{candidate.get('name', candidate_id)} → {target}")
        sync_status = await _sync_workflow_to_bitable(candidate)
        return {"candidate": candidate, "bitable_sync": sync_status}
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail="候选人不存在")


@router.delete("/workflow/candidates/{candidate_id}")
async def delete_workflow_candidate(candidate_id: str):
    """Permanently remove one candidate from this machine's recruitment data."""
    candidate = ops_store.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不存在或已被删除")

    synced_interviews = [
        interview for interview in ops_store.list_interviews(candidate_id)
        if interview.get("calendar_event_id")
    ]
    if synced_interviews:
        raise HTTPException(
            status_code=409,
            detail="该候选人存在已同步到飞书日历的面试，请先在飞书取消日程后再删除本地候选人。",
        )

    file_record = ops_store.get_resume_file(candidate_id)
    try:
        await run_in_threadpool(retriever.remove_resume, candidate_id)
        deleted = ops_store.delete_candidate(candidate_id)
        ops_store.delete_resume_identity(candidate_id)
        ops_store.delete_resume_file(candidate_id)
        if isinstance(file_record, dict) and file_record.get("relative_path"):
            await run_in_threadpool(resume_file_store.delete, file_record["relative_path"])
        resume_storage.pop(candidate_id, None)
        ops_store.log("candidate_delete", "success", f"已删除本地候选人：{candidate.get('name') or candidate_id}")
        return {"deleted": True, "candidate_id": candidate_id, "cleanup": deleted}
    except KeyError:
        raise HTTPException(status_code=404, detail="候选人不存在或已被删除")
    except Exception:
        logger.exception("删除本地候选人失败")
        raise HTTPException(status_code=500, detail="删除候选人失败，本地数据未完全清理，请稍后重试。")


@router.post("/workflow/interviews")
async def schedule_interview(request: InterviewCreateRequest):
    if request.end_at <= request.start_at:
        raise HTTPException(status_code=400, detail="面试结束时间必须晚于开始时间")
    candidate = ops_store.get_candidate(request.candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不存在")
    try:
        validate_schedule(
            candidate["status"], ops_store.list_interviews(request.candidate_id),
            request.round_name,
        )
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    # Always check conflicts already scheduled in HireMS before creating an event.
    for existing in ops_store.list_interviews():
        overlap = request.start_at < datetime.fromisoformat(existing["end_at"]) and request.end_at > datetime.fromisoformat(existing["start_at"])
        shared_interviewer = set(request.interviewer_ids) & set(existing["interviewer_ids"])
        if overlap and shared_interviewer and existing["status"] == "已安排":
            raise HTTPException(status_code=409, detail="所选时段与 HireMS 已安排的面试冲突")
    if request.interviewer_ids and feishu_workflow.configured_for_calendar():
        try:
            busy = await run_in_threadpool(feishu_workflow.busy_interviewers, request.start_at, request.end_at, request.interviewer_ids)
            if busy:
                raise HTTPException(status_code=409, detail=f"以下面试官在飞书日历中忙碌：{', '.join(busy)}")
        except ValueError as config_error:
            raise HTTPException(status_code=400, detail=str(config_error))
    interview = {
        "interview_id": str(uuid.uuid4()), "candidate_id": request.candidate_id, "round_name": request.round_name,
        "interviewer_ids": request.interviewer_ids, "start_at": request.start_at.isoformat(), "end_at": request.end_at.isoformat(),
        "location": request.location, "note": request.note, "status": "已安排",
    }
    try:
        interview["calendar_event_id"] = await run_in_threadpool(feishu_workflow.create_interview_event, candidate, interview)
        sync_status = "calendar_synced"
    except ValueError:
        sync_status = "local_only"
    except Exception as calendar_error:
        logger.warning(f"日历创建失败: {calendar_error}")
        raise HTTPException(status_code=502, detail=f"飞书日历创建失败：{calendar_error}")
    stored = ops_store.create_interview(interview)
    candidate = ops_store.update_candidate(request.candidate_id, "安排面试")
    ops_store.log("interview_schedule", "success", f"{candidate.get('name', '')} {request.round_name}：{sync_status}")
    email_status = await _send_candidate_interview_email(candidate, stored, "scheduled")
    bitable_sync = await _sync_workflow_to_bitable(candidate, stored)
    return {
        "interview": stored, "candidate": candidate, "sync_status": sync_status,
        "email_status": email_status, "bitable_sync": bitable_sync,
    }


@router.patch("/workflow/interviews/{interview_id}")
async def reschedule_interview(interview_id: str, request: InterviewRescheduleRequest):
    if request.end_at <= request.start_at:
        raise HTTPException(status_code=400, detail="面试结束时间必须晚于开始时间")
    interview = ops_store.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试记录不存在")
    if interview["status"] != "已安排":
        raise HTTPException(status_code=409, detail="只有已安排且未结束的面试可以改期")
    candidate = ops_store.get_candidate(interview["candidate_id"])
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不存在")

    for existing in ops_store.list_interviews():
        if existing["interview_id"] == interview_id or existing["status"] != "已安排":
            continue
        overlap = (
            request.start_at < datetime.fromisoformat(existing["end_at"])
            and request.end_at > datetime.fromisoformat(existing["start_at"])
        )
        if overlap and set(request.interviewer_ids) & set(existing["interviewer_ids"]):
            raise HTTPException(status_code=409, detail="所选时段与 HireMS 已安排的面试冲突")
    if request.interviewer_ids and feishu_workflow.configured_for_calendar():
        busy = await run_in_threadpool(
            feishu_workflow.busy_interviewers,
            request.start_at, request.end_at, request.interviewer_ids,
        )
        if busy:
            raise HTTPException(status_code=409, detail=f"以下面试官在飞书日历中忙碌：{', '.join(busy)}")

    updated_values = {
        **interview, "interviewer_ids": request.interviewer_ids,
        "start_at": request.start_at.isoformat(), "end_at": request.end_at.isoformat(),
        "location": request.location, "note": request.note,
    }
    sync_status = "local_only"
    try:
        if interview.get("calendar_event_id"):
            await run_in_threadpool(
                feishu_workflow.update_interview_event,
                interview["calendar_event_id"], candidate, updated_values,
            )
            sync_status = "calendar_synced"
        elif feishu_workflow.configured_for_calendar():
            updated_values["calendar_event_id"] = await run_in_threadpool(
                feishu_workflow.create_interview_event, candidate, updated_values,
            )
            sync_status = "calendar_synced"
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"飞书日历改期失败：{error}")

    stored = ops_store.reschedule_interview(interview_id, updated_values)
    ops_store.log("interview_reschedule", "success", f"{candidate.get('name', '')} {stored['round_name']}")
    email_status = await _send_candidate_interview_email(candidate, stored, "rescheduled")
    bitable_sync = await _sync_workflow_to_bitable(candidate, stored)
    return {
        "interview": stored, "candidate": candidate, "sync_status": sync_status,
        "email_status": email_status, "bitable_sync": bitable_sync,
    }


@router.post("/workflow/interviews/{interview_id}/cancel")
async def cancel_interview(interview_id: str, request: InterviewCancelRequest):
    interview = ops_store.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试记录不存在")
    if interview["status"] != "已安排":
        raise HTTPException(status_code=409, detail="只有已安排的面试可以取消")
    candidate = ops_store.get_candidate(interview["candidate_id"])
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不存在")
    if interview.get("calendar_event_id"):
        try:
            await run_in_threadpool(
                feishu_workflow.cancel_interview_event, interview["calendar_event_id"],
            )
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"飞书日历取消失败：{error}")

    stored = ops_store.cancel_interview(interview_id, request.reason.strip())
    previous = [
        item for item in ops_store.list_interviews(candidate["id"])
        if item["interview_id"] != interview_id and item["status"] not in {"已取消", "已安排"}
    ]
    candidate = ops_store.update_candidate(candidate["id"], "面试中" if previous else "通过")
    ops_store.log("interview_cancel", "success", f"{candidate.get('name', '')} {stored['round_name']}")
    email_status = await _send_candidate_interview_email(candidate, stored, "cancelled")
    bitable_sync = await _sync_workflow_to_bitable(candidate, stored)
    return {
        "interview": stored, "candidate": candidate,
        "email_status": email_status, "bitable_sync": bitable_sync,
    }


@router.post("/workflow/interviews/{interview_id}/feedback")
async def submit_interview_feedback(interview_id: str, request: InterviewFeedbackRequest):
    if not request.feedback.strip():
        raise HTTPException(status_code=400, detail="请填写面试评价后再提交结论")
    try:
        current = ops_store.get_interview(interview_id)
        if not current:
            raise KeyError(interview_id)
        if current["status"] not in {"已安排", "待定"}:
            raise InvalidTransition(f"该面试当前为“{current['status']}”，不能重复提交评价")
        workflow_status = feedback_target(current["round_name"], request.status)
        interview = ops_store.update_interview(interview_id, request.status, request.feedback)
        candidate = ops_store.update_candidate(interview["candidate_id"], workflow_status)
        ops_store.log(
            "interview_feedback", "success",
            f"{candidate.get('name', '')} {interview['round_name']} → {request.status}",
        )
        bitable_sync = await _sync_workflow_to_bitable(candidate, interview)
        return {"interview": interview, "candidate": candidate, "bitable_sync": bitable_sync}
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail="面试记录不存在")


@router.post("/workflow/candidates/{candidate_id}/offer")
async def update_offer(candidate_id: str, request: OfferUpdateRequest):
    try:
        current = ops_store.get_candidate(candidate_id)
        if not current:
            raise KeyError(candidate_id)
        target = offer_target(current["status"], request.status)
        candidate = ops_store.update_candidate(candidate_id, target)
        ops_store.log("offer", "success", f"{candidate.get('name', candidate_id)} → {target}")
        bitable_sync = await _sync_workflow_to_bitable(candidate)
        return {"candidate": candidate, "bitable_sync": bitable_sync}
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail="候选人不存在")


def _daily_summary() -> str:
    cutoff = datetime.now() - timedelta(days=1)
    candidates = ops_store.list_candidates()
    changed = [c for c in candidates if datetime.fromisoformat(c["updated_at"]) >= cutoff]
    by_status: dict[str, int] = {}
    for candidate in changed:
        by_status[candidate["status"]] = by_status.get(candidate["status"], 0) + 1
    detail = "、".join(f"{key} {value}" for key, value in by_status.items()) or "无状态变更"
    return f"HireMS 昨日招聘汇总\n处理候选人 {len(changed)} 位：{detail}\n待复核 {len(ops_store.list_candidates('待复核'))} 位。"


@router.post("/workflow/notifications/{kind}")
async def run_notifications(kind: str):
    if kind == "daily_summary":
        text = _daily_summary()
        try:
            count = await run_in_threadpool(feishu_workflow.send_text, text)
            ops_store.notification(None, kind, "feishu", "success", text)
        except ValueError:
            count = 0; ops_store.notification(None, kind, "local", "pending", text)
        return {"sent": count, "summary": text}
    if kind == "overdue":
        stale = ops_store.stale_candidates(settings.NOTIFY_OVERDUE_HOURS)
        if not stale:
            return {"sent": 0, "summary": "没有超时未处理的简历"}
        text = f"HireMS 提醒：有 {len(stale)} 位候选人已超过 {settings.NOTIFY_OVERDUE_HOURS} 小时未处理，请尽快复核。"
        try:
            count = await run_in_threadpool(feishu_workflow.send_text, text)
            ops_store.notification(None, kind, "feishu", "success", text)
        except ValueError:
            count = 0; ops_store.notification(None, kind, "local", "pending", text)
        return {"sent": count, "summary": text}
    if kind == "interview_reminder":
        now = datetime.now().astimezone()
        end = now + timedelta(hours=1, minutes=5)
        due = []
        for item in ops_store.list_interviews():
            start = datetime.fromisoformat(item["start_at"])
            if start.tzinfo is None:
                start = start.astimezone()
            if now <= start <= end and item["status"] == "已安排":
                due.append(item)
        count = 0
        for interview in due:
            candidate = ops_store.get_candidate(interview["candidate_id"])
            text = f"面试提醒：{interview['round_name']}将在 1 小时内开始，候选人：{candidate.get('name') if candidate else ''}。"
            reminder_kind = f"interview_reminder:{interview['interview_id']}"
            if not ops_store.has_notification(interview["candidate_id"], reminder_kind, "feishu"):
                try:
                    count += await run_in_threadpool(feishu_workflow.send_text, text, interview["interviewer_ids"] or None)
                    ops_store.notification(interview["candidate_id"], reminder_kind, "feishu", "success", text)
                except ValueError:
                    ops_store.notification(interview["candidate_id"], reminder_kind, "local", "pending", text)
            if (
                candidate and candidate.get("email") and candidate_email.configured()
                and not ops_store.has_notification(interview["candidate_id"], reminder_kind, "email")
            ):
                try:
                    await run_in_threadpool(candidate_email.interview_reminder, candidate, interview)
                    ops_store.notification(interview["candidate_id"], reminder_kind, "email", "success", candidate["email"])
                except Exception as error:
                    ops_store.notification(interview["candidate_id"], reminder_kind, "email", "failed", str(error))
        return {"sent": count, "summary": f"检查到 {len(due)} 场即将开始的面试"}
    raise HTTPException(status_code=404, detail="未知的提醒任务")


@router.post("/feishu/card-actions")
async def receive_feishu_card_action(request: Request):
    """Receives card.action.trigger callbacks configured in the Feishu app console."""
    payload = await request.json()
    token = (payload.get("header") or {}).get("token")
    if settings.FEISHU_CALLBACK_TOKEN and token != settings.FEISHU_CALLBACK_TOKEN:
        raise HTTPException(status_code=401, detail="飞书回调 Token 校验失败")
    value = ((payload.get("event") or {}).get("action") or {}).get("value") or {}
    candidate_id, action = value.get("candidate_id"), value.get("action")
    if not candidate_id or action not in {"pass", "reject", "schedule"}:
        raise HTTPException(status_code=400, detail="卡片动作参数错误")
    if action == "schedule":
        raise HTTPException(status_code=409, detail="请在 HireMS 中选择轮次和时间后安排面试")
    try:
        current = ops_store.get_candidate(candidate_id)
        if not current:
            raise KeyError(candidate_id)
        target = candidate_action_target(current["status"], action)
        candidate = ops_store.update_candidate(candidate_id, target)
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error))
    except KeyError:
        raise HTTPException(status_code=404, detail="候选人不存在")
    operator = ((payload.get("event") or {}).get("operator") or {}).get("open_id")
    ops_store.log("feishu_card_action", "success", f"{operator or 'HR'} 将 {candidate.get('name', candidate_id)} 标记为 {candidate['status']}")
    await _sync_workflow_to_bitable(candidate)
    return {"toast": {"type": "success", "content": f"已更新为：{candidate['status']}"}}


@router.post("/operations/bitable-export")
async def export_to_bitable(request: BitableExportRequest):
    """Idempotently sync the cached screening result without another LLM run."""
    try:
        screening = await get_screening_results(request.query_id)
        candidates = [candidate.model_dump() for candidate in screening.candidates]
        sync_result = await run_in_threadpool(
            _sync_candidates_to_bitable, request.query_id, candidates, request.job_name,
        )
        cached = query_storage.get(request.query_id, {}).get("screening_result")
        if isinstance(cached, dict):
            cached["bitable_sync"] = sync_result
        exported = sync_result["exported"]
        message = (
            f"已写入 {exported} 条高分候选人记录"
            if exported else "多维表格已是最新，无需重复写入"
        )
        return {**sync_result, "message": message}
    except HTTPException:
        raise
    except PermissionError as e:
        ops_store.log("bitable_export", "blocked", str(e))
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        ops_store.log("bitable_export", "blocked", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("多维表格写入失败")
        ops_store.log("bitable_export", "failed", str(e))
        raise HTTPException(status_code=502, detail=f"多维表格写入失败：{e}")


@router.get("/resumes/{resume_id}/file")
async def get_resume_file(resume_id: str, download: bool = False):
    """Preview or download an imported original without exposing its disk path."""
    record = ops_store.get_resume_file(resume_id)
    if not isinstance(record, dict):
        raise HTTPException(
            status_code=404,
            detail="未找到该候选人的原始简历；历史数据请重新导入一次。",
        )
    try:
        path = resume_file_store.resolve(record["relative_path"])
    except (FileNotFoundError, ValueError, KeyError):
        logger.warning(f"Resume original is missing or invalid: {resume_id}")
        raise HTTPException(status_code=404, detail="原始简历文件不存在，请重新导入。")

    force_download = download or path.suffix.lower() == ".docx"
    return FileResponse(
        path=path,
        media_type=record.get("media_type") or "application/octet-stream",
        filename=record.get("original_filename") or path.name,
        content_disposition_type="attachment" if force_download else "inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


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
