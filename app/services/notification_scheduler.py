"""In-process scheduled notifications for the local HireMS service."""
from __future__ import annotations

from datetime import datetime, timedelta

from config.config import settings
from app.services.feishu_workflow import FeishuWorkflowClient
from app.services.candidate_email import CandidateEmailNotifier
from app.services.intake_store import IntakeStore


class NotificationScheduler:
    def __init__(self) -> None:
        self.store = IntakeStore()
        self.feishu = FeishuWorkflowClient()
        self.candidate_email = CandidateEmailNotifier()
        self.scheduler = None

    def start(self) -> None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            return
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.scheduler.add_job(self.daily_summary, "cron", hour=9, minute=0, id="daily_summary", replace_existing=True)
        self.scheduler.add_job(self.overdue_reminder, "interval", minutes=30, id="overdue_reminder", replace_existing=True)
        self.scheduler.add_job(self.interview_reminder, "interval", minutes=5, id="interview_reminder", replace_existing=True)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)

    def daily_summary(self) -> None:
        if not self.feishu.configured_for_messages():
            return
        cutoff = datetime.now() - timedelta(days=1)
        changed = [c for c in self.store.list_candidates() if datetime.fromisoformat(c["updated_at"]) >= cutoff]
        by_status: dict[str, int] = {}
        for candidate in changed:
            by_status[candidate["status"]] = by_status.get(candidate["status"], 0) + 1
        detail = "、".join(f"{status} {count}" for status, count in by_status.items()) or "无状态变更"
        text = f"HireMS 昨日招聘汇总\n处理候选人 {len(changed)} 位：{detail}\n待复核 {len(self.store.list_candidates('待复核'))} 位。"
        try:
            self.feishu.send_text(text)
            self.store.notification(None, "daily_summary", "feishu", "success", text)
        except Exception as exc:
            self.store.notification(None, "daily_summary", "feishu", "failed", str(exc))

    def overdue_reminder(self) -> None:
        if not self.feishu.configured_for_messages():
            return
        stale = self.store.stale_candidates(settings.NOTIFY_OVERDUE_HOURS)
        if not stale:
            return
        text = f"HireMS 提醒：有 {len(stale)} 位候选人已超过 {settings.NOTIFY_OVERDUE_HOURS} 小时未处理，请尽快复核。"
        try:
            self.feishu.send_text(text)
            self.store.notification(None, "overdue", "feishu", "success", text)
        except Exception as exc:
            self.store.notification(None, "overdue", "feishu", "failed", str(exc))

    def interview_reminder(self) -> None:
        if not self.feishu.configured_for_messages():
            return
        now = datetime.now().astimezone()
        due_by = now + timedelta(hours=1, minutes=5)
        for interview in self.store.list_interviews():
            start = datetime.fromisoformat(interview["start_at"])
            if start.tzinfo is None:
                start = start.astimezone()
            if not (now <= start <= due_by and interview["status"] == "已安排"):
                continue
            candidate = self.store.get_candidate(interview["candidate_id"])
            text = f"面试提醒：{interview['round_name']}将在 1 小时内开始，候选人：{candidate.get('name') if candidate else ''}。"
            kind = f"interview_reminder:{interview['interview_id']}"
            if not self.store.has_notification(interview["candidate_id"], kind, "feishu"):
                try:
                    self.feishu.send_text(text, interview["interviewer_ids"] or None)
                    self.store.notification(interview["candidate_id"], kind, "feishu", "success", text)
                except Exception as exc:
                    self.store.notification(interview["candidate_id"], kind, "feishu", "failed", str(exc))
            if (
                candidate and candidate.get("email") and self.candidate_email.configured()
                and not self.store.has_notification(interview["candidate_id"], kind, "email")
            ):
                try:
                    self.candidate_email.interview_reminder(candidate, interview)
                    self.store.notification(
                        interview["candidate_id"], kind, "email", "success", candidate["email"],
                    )
                except Exception as exc:
                    self.store.notification(interview["candidate_id"], kind, "email", "failed", str(exc))
