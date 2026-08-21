"""SMTP notifications sent only to external candidates."""
from __future__ import annotations

import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from config.config import settings


class CandidateEmailNotifier:
    @staticmethod
    def configured() -> bool:
        return bool(
            settings.CANDIDATE_EMAIL_NOTIFICATIONS
            and settings.MAIL_SMTP_HOST
            and settings.MAIL_SMTP_USER
            and settings.MAIL_SMTP_PASSWORD
        )

    def send(self, recipient: str, subject: str, content: str) -> None:
        if not recipient:
            raise ValueError("候选人没有邮箱地址")
        if not self.configured():
            raise ValueError("候选人邮件通知尚未配置 SMTP")
        message = EmailMessage()
        message["From"] = formataddr((settings.MAIL_SMTP_FROM_NAME, settings.MAIL_SMTP_USER))
        message["To"] = recipient
        contact_emails = [
            item.strip() for item in settings.HR_CONTACT_EMAILS.split(",") if item.strip()
        ]
        if contact_emails:
            message["Reply-To"] = contact_emails[0]
        message["Subject"] = subject
        message.set_content(content)
        if settings.MAIL_SMTP_USE_SSL:
            with smtplib.SMTP_SSL(
                settings.MAIL_SMTP_HOST, settings.MAIL_SMTP_PORT, timeout=20,
            ) as client:
                client.login(settings.MAIL_SMTP_USER, settings.MAIL_SMTP_PASSWORD)
                client.send_message(message)
        else:
            with smtplib.SMTP(
                settings.MAIL_SMTP_HOST, settings.MAIL_SMTP_PORT, timeout=20,
            ) as client:
                client.starttls()
                client.login(settings.MAIL_SMTP_USER, settings.MAIL_SMTP_PASSWORD)
                client.send_message(message)

    @staticmethod
    def _time(value: str) -> str:
        return datetime.fromisoformat(value).astimezone().strftime("%Y年%m月%d日 %H:%M")

    def interview_scheduled(self, candidate: dict, interview: dict, action: str = "安排") -> None:
        name = candidate.get("name") or "候选人"
        content = (
            f"{name}，您好：\n\n"
            f"您的{interview['round_name']}已{action}。\n"
            f"时间：{self._time(interview['start_at'])} - {self._time(interview['end_at'])}\n"
            f"地点/会议链接：{interview.get('location') or '待通知'}\n"
            f"备注：{interview.get('note') or '无'}\n\n"
            "如时间不便，请直接回复本邮件联系招聘团队。"
        )
        self.send(candidate.get("email") or "", f"面试{action}通知｜{interview['round_name']}", content)

    def interview_cancelled(self, candidate: dict, interview: dict) -> None:
        name = candidate.get("name") or "候选人"
        content = (
            f"{name}，您好：\n\n原定于 {self._time(interview['start_at'])} 的"
            f"{interview['round_name']}已取消。\n"
            f"原因：{interview.get('cancel_reason') or '招聘团队后续将另行联系'}\n\n"
            "如有疑问，请直接回复本邮件。"
        )
        self.send(candidate.get("email") or "", f"面试取消通知｜{interview['round_name']}", content)

    def interview_reminder(self, candidate: dict, interview: dict) -> None:
        name = candidate.get("name") or "候选人"
        content = (
            f"{name}，您好：\n\n您的{interview['round_name']}将在约 1 小时后开始。\n"
            f"时间：{self._time(interview['start_at'])}\n"
            f"地点/会议链接：{interview.get('location') or '待通知'}\n\n"
            "请提前做好准备。"
        )
        self.send(candidate.get("email") or "", f"面试提醒｜{interview['round_name']}", content)
