from fastapi.testclient import TestClient

from app.main import app
from app.services.intake_store import IntakeStore
from config.config import settings


client = TestClient(app)


def test_safe_system_settings_are_persisted_and_applied(tmp_path, monkeypatch):
    from app.api import routes

    store = IntakeStore(str(tmp_path / "settings.sqlite3"))
    monkeypatch.setattr(routes, "ops_store", store)
    for attribute in (
        "FEISHU_HR_RECEIVER_IDS", "HR_CONTACT_EMAILS", "DEFAULT_INTERVIEWER_IDS",
        "DEFAULT_INTERVIEW_LOCATION", "MAIL_SMTP_FROM_NAME", "NOTIFY_OVERDUE_HOURS",
    ):
        monkeypatch.setattr(settings, attribute, getattr(settings, attribute))
    payload = {
        "hr_open_ids": ["ou_hr_1", "ou_hr_2"],
        "hr_emails": ["hr@example.com"],
        "default_interviewer_ids": ["ou_interviewer"],
        "default_interview_location": "腾讯会议 123-456",
        "mail_from_name": "星河招聘团队",
        "overdue_hours": 36,
    }

    response = client.put("/api/v1/settings", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["hr_open_ids"] == ["ou_hr_1", "ou_hr_2"]
    assert data["default_interview_location"] == "腾讯会议 123-456"
    assert data["credentials_managed_in_env"] is True
    assert "password" not in data
    assert settings.FEISHU_HR_RECEIVER_IDS == "ou_hr_1,ou_hr_2"
    assert settings.NOTIFY_OVERDUE_HOURS == 36

    reopened = IntakeStore(str(tmp_path / "settings.sqlite3"))
    assert reopened.get_system_setting("recruiting_preferences")["hr_emails"] == [
        "hr@example.com",
    ]
    loaded = client.get("/api/v1/settings")
    assert loaded.status_code == 200
    assert loaded.json()["default_interviewer_ids"] == ["ou_interviewer"]


def test_system_settings_reject_invalid_hr_email(tmp_path, monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes, "ops_store", IntakeStore(str(tmp_path / "settings.sqlite3")))
    response = client.put("/api/v1/settings", json={
        "hr_open_ids": [], "hr_emails": ["not-an-email"],
        "default_interviewer_ids": [], "default_interview_location": "线上",
        "mail_from_name": "招聘团队", "overdue_hours": 48,
    })
    assert response.status_code == 400
    assert "邮箱格式" in response.json()["detail"]
