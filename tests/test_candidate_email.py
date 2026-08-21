from app.services.candidate_email import CandidateEmailNotifier


def test_candidate_email_uses_smtp_and_contains_interview_details(monkeypatch):
    sent = {}

    class SMTP:
        def __init__(self, host, port, timeout):
            sent.update({"host": host, "port": port, "timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login(self, user, password):
            sent.update({"user": user, "password": password})

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr("app.services.candidate_email.smtplib.SMTP_SSL", SMTP)
    monkeypatch.setattr("app.services.candidate_email.settings.MAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.services.candidate_email.settings.MAIL_SMTP_PORT", 465)
    monkeypatch.setattr("app.services.candidate_email.settings.MAIL_SMTP_USER", "hr@example.com")
    monkeypatch.setattr("app.services.candidate_email.settings.MAIL_SMTP_PASSWORD", "secret")
    monkeypatch.setattr("app.services.candidate_email.settings.MAIL_SMTP_USE_SSL", True)
    monkeypatch.setattr("app.services.candidate_email.settings.CANDIDATE_EMAIL_NOTIFICATIONS", True)

    CandidateEmailNotifier().interview_scheduled(
        {"name": "张三", "email": "candidate@example.com"},
        {
            "round_name": "一面", "start_at": "2030-09-10T10:00:00+08:00",
            "end_at": "2030-09-10T11:00:00+08:00", "location": "线上会议",
            "note": "请提前测试设备",
        },
    )

    assert sent["host"] == "smtp.example.com"
    assert sent["message"]["To"] == "candidate@example.com"
    assert "一面" in sent["message"]["Subject"]
    assert "线上会议" in sent["message"].get_content()
