"""
API 路由
"""
import asyncio
import uuid
import json
from datetime import datetime, timedelta
from typing import Any, Dict
import os
import tempfile

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.models import (
    UploadResumeResponse, QueryRequest, QueryResponse, ScreeningResult, BitableExportRequest,
    CandidateActionRequest, InterviewCreateRequest, InterviewFeedbackRequest, OfferUpdateRequest,
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
from app.services.feishu_workflow import FeishuWorkflowClient
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
feishu_workflow = FeishuWorkflowClient()

WORKFLOW_ACTIONS = {
    "pass": "通过", "reject": "淘汰", "schedule": "安排面试",
    "offer_pending": "Offer待发", "offer_sent": "Offer已发",
    "offer_accepted": "Offer已接受", "offer_rejected": "Offer已拒绝",
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
        status_code, detail = _friendly_ingest_error(e)
        raise HTTPException(status_code=status_code, detail=detail)


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

        # The result page is also the workflow entry point: save every candidate
        # for human review and notify the assigned HR only the first time it appears.
        for candidate in candidates:
            workflow_candidate = ops_store.upsert_candidate(candidate, query_data["text"])
            if workflow_candidate.pop("_created", False):
                try:
                    sent = await run_in_threadpool(feishu_workflow.send_candidate_card, workflow_candidate)
                    ops_store.notification(candidate["id"], "new_candidate", "feishu_card", "success", f"已推送给 {sent} 位 HR")
                except ValueError:
                    ops_store.notification(candidate["id"], "new_candidate", "local_queue", "pending", "飞书未配置，已保留在本地待复核队列")
                except Exception as notification_error:
                    logger.warning(f"新候选人卡片推送失败: {notification_error}")
                    ops_store.notification(candidate["id"], "new_candidate", "feishu_card", "failed", str(notification_error))

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


@router.get("/workflow/candidates")
async def list_workflow_candidates(status: str | None = None):
    return {"candidates": ops_store.list_candidates(status), "total": len(ops_store.list_candidates(status))}


@router.get("/workflow/candidates/{candidate_id}")
async def get_workflow_candidate(candidate_id: str):
    candidate = ops_store.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选人不在工作流队列中")
    return {"candidate": candidate, "interviews": ops_store.list_interviews(candidate_id)}


@router.post("/workflow/candidates/{candidate_id}/action")
async def update_candidate_action(candidate_id: str, request: CandidateActionRequest):
    if request.action not in WORKFLOW_ACTIONS:
        raise HTTPException(status_code=400, detail="不支持的候选人操作")
    try:
        candidate = ops_store.update_candidate(candidate_id, WORKFLOW_ACTIONS[request.action], request.owner_id)
        ops_store.log("candidate_action", "success", f"{candidate.get('name', candidate_id)} → {WORKFLOW_ACTIONS[request.action]}")
        return {"candidate": candidate}
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

    try:
        await run_in_threadpool(retriever.remove_resume, candidate_id)
        deleted = ops_store.delete_candidate(candidate_id)
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
    ops_store.update_candidate(request.candidate_id, "安排面试")
    ops_store.log("interview_schedule", "success", f"{candidate.get('name', '')} {request.round_name}：{sync_status}")
    return {"interview": stored, "sync_status": sync_status}


@router.post("/workflow/interviews/{interview_id}/feedback")
async def submit_interview_feedback(interview_id: str, request: InterviewFeedbackRequest):
    if request.status not in {"通过", "淘汰", "待定", "下一轮"}:
        raise HTTPException(status_code=400, detail="面试结论必须是通过、淘汰、待定或下一轮")
    try:
        interview = ops_store.update_interview(interview_id, request.status, request.feedback)
        workflow_status = "Offer待发" if request.status == "通过" else ("淘汰" if request.status == "淘汰" else "面试中")
        candidate = ops_store.update_candidate(interview["candidate_id"], workflow_status)
        return {"interview": interview, "candidate": candidate}
    except KeyError:
        raise HTTPException(status_code=404, detail="面试记录不存在")


@router.post("/workflow/candidates/{candidate_id}/offer")
async def update_offer(candidate_id: str, request: OfferUpdateRequest):
    allowed = {"待发": "Offer待发", "已发": "Offer已发", "已接受": "Offer已接受", "已拒绝": "Offer已拒绝"}
    if request.status not in allowed:
        raise HTTPException(status_code=400, detail="Offer 状态不正确")
    try:
        candidate = ops_store.update_candidate(candidate_id, allowed[request.status])
        ops_store.log("offer", "success", f"{candidate.get('name', candidate_id)} → {allowed[request.status]}")
        return {"candidate": candidate}
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
        now, end = datetime.now(), datetime.now() + timedelta(hours=1, minutes=5)
        due = [i for i in ops_store.list_interviews() if now <= datetime.fromisoformat(i["start_at"]) <= end and i["status"] == "已安排"]
        count = 0
        for interview in due:
            candidate = ops_store.get_candidate(interview["candidate_id"])
            text = f"面试提醒：{interview['round_name']}将在 1 小时内开始，候选人：{candidate.get('name') if candidate else ''}。"
            try:
                count += await run_in_threadpool(feishu_workflow.send_text, text, interview["interviewer_ids"] or None)
                ops_store.notification(interview["candidate_id"], kind, "feishu", "success", text)
            except ValueError:
                ops_store.notification(interview["candidate_id"], kind, "local", "pending", text)
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
    try:
        candidate = ops_store.update_candidate(candidate_id, WORKFLOW_ACTIONS[action])
    except KeyError:
        raise HTTPException(status_code=404, detail="候选人不存在")
    operator = ((payload.get("event") or {}).get("operator") or {}).get("open_id")
    ops_store.log("feishu_card_action", "success", f"{operator or 'HR'} 将 {candidate.get('name', candidate_id)} 标记为 {candidate['status']}")
    return {"toast": {"type": "success", "content": f"已更新为：{candidate['status']}"}}


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
