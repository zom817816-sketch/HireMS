"""Safe, mutable recruiting settings persisted in the local operations DB."""
from __future__ import annotations

import re
from typing import Any

from config.config import settings
from app.services.intake_store import IntakeStore


SETTINGS_KEY = "recruiting_preferences"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _clean_list(values: list[str], label: str, maximum: int = 50) -> list[str]:
    unique: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in unique:
            continue
        if len(value) > 128:
            raise ValueError(f"{label}中的单项内容不能超过 128 个字符")
        unique.append(value)
    if len(unique) > maximum:
        raise ValueError(f"{label}最多填写 {maximum} 项")
    return unique


def default_runtime_settings() -> dict[str, Any]:
    return {
        "hr_open_ids": _split(settings.FEISHU_HR_RECEIVER_IDS),
        "hr_emails": _split(settings.HR_CONTACT_EMAILS),
        "default_interviewer_ids": _split(settings.DEFAULT_INTERVIEWER_IDS),
        "default_interview_location": settings.DEFAULT_INTERVIEW_LOCATION,
        "mail_from_name": settings.MAIL_SMTP_FROM_NAME,
        "overdue_hours": settings.NOTIFY_OVERDUE_HOURS,
    }


def normalize_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    hr_open_ids = _clean_list(values.get("hr_open_ids") or [], "HR OpenID")
    interviewer_ids = _clean_list(
        values.get("default_interviewer_ids") or [], "默认面试官 OpenID",
    )
    hr_emails = _clean_list(values.get("hr_emails") or [], "HR 邮箱")
    invalid_emails = [email for email in hr_emails if not _EMAIL_PATTERN.match(email)]
    if invalid_emails:
        raise ValueError(f"HR 邮箱格式不正确：{invalid_emails[0]}")

    location = str(values.get("default_interview_location") or "线上").strip()
    from_name = str(values.get("mail_from_name") or "招聘团队").strip()
    if len(location) > 100:
        raise ValueError("默认面试地点不能超过 100 个字符")
    if not from_name or len(from_name) > 50:
        raise ValueError("邮件发件人名称应为 1-50 个字符")
    try:
        overdue_hours = int(values.get("overdue_hours", 48))
    except (TypeError, ValueError) as exc:
        raise ValueError("超时提醒小时数必须是整数") from exc
    if not 1 <= overdue_hours <= 720:
        raise ValueError("超时提醒小时数必须在 1-720 之间")

    return {
        "hr_open_ids": hr_open_ids,
        "hr_emails": hr_emails,
        "default_interviewer_ids": interviewer_ids,
        "default_interview_location": location,
        "mail_from_name": from_name,
        "overdue_hours": overdue_hours,
    }


def apply_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_runtime_settings(values)
    settings.FEISHU_HR_RECEIVER_IDS = ",".join(normalized["hr_open_ids"])
    settings.HR_CONTACT_EMAILS = ",".join(normalized["hr_emails"])
    settings.DEFAULT_INTERVIEWER_IDS = ",".join(normalized["default_interviewer_ids"])
    settings.DEFAULT_INTERVIEW_LOCATION = normalized["default_interview_location"]
    settings.MAIL_SMTP_FROM_NAME = normalized["mail_from_name"]
    settings.NOTIFY_OVERDUE_HOURS = normalized["overdue_hours"]
    return normalized


def load_runtime_settings(store: IntakeStore) -> dict[str, Any]:
    saved = store.get_system_setting(SETTINGS_KEY)
    return apply_runtime_settings(saved if isinstance(saved, dict) else default_runtime_settings())


def save_runtime_settings(store: IntakeStore, values: dict[str, Any]) -> dict[str, Any]:
    normalized = apply_runtime_settings(values)
    store.set_system_setting(SETTINGS_KEY, normalized)
    return normalized

