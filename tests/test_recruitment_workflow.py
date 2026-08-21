from fastapi.testclient import TestClient

from app.main import app
from app.services.intake_store import IntakeStore
from app.services.recruitment_state import (
    InvalidTransition, candidate_action_target, expected_next_round,
    feedback_target, offer_target, validate_schedule,
)


client = TestClient(app)


class LocalCalendar:
    @staticmethod
    def configured_for_calendar():
        return False

    @staticmethod
    def create_interview_event(*_args):
        raise ValueError("not configured")


def _candidate(store: IntakeStore, candidate_id: str = "candidate-flow") -> dict:
    return store.upsert_candidate({
        "id": candidate_id, "name": "候选人", "email": "",
        "overall_score": 0.9,
    }, "课程销售顾问")


def _schedule(candidate_id: str, round_name: str, day: int) -> dict:
    response = client.post("/api/v1/workflow/interviews", json={
        "candidate_id": candidate_id, "round_name": round_name,
        "interviewer_ids": ["ou_interviewer"],
        "start_at": f"2030-09-{day:02d}T10:00:00+08:00",
        "end_at": f"2030-09-{day:02d}T11:00:00+08:00",
        "location": "线上会议", "note": "测试",
    })
    assert response.status_code == 200, response.text
    return response.json()["interview"]


def _feedback(interview_id: str, status: str, next_step: str | None = None) -> dict:
    payload = {"status": status, "feedback": "能力证据充分，沟通清晰。"}
    if next_step:
        payload["next_step"] = next_step
    response = client.post(
        f"/api/v1/workflow/interviews/{interview_id}/feedback",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()["candidate"]


def test_hr_manually_chooses_continue_interview_or_offer(tmp_path, monkeypatch):
    from app.api import routes

    store = IntakeStore(str(tmp_path / "workflow.sqlite3"))
    _candidate(store)
    monkeypatch.setattr(routes, "ops_store", store)
    monkeypatch.setattr(routes, "feishu_workflow", LocalCalendar())

    first = _schedule("candidate-flow", "业务面", 10)
    assert store.get_candidate("candidate-flow")["status"] == "安排面试"

    missing_decision = client.post(
        f"/api/v1/workflow/interviews/{first['interview_id']}/feedback",
        json={"status": "通过", "feedback": "建议继续考察。"},
    )
    assert missing_decision.status_code == 409
    assert "下一环节" in missing_decision.json()["detail"]
    assert _feedback(first["interview_id"], "通过", "继续面试")["status"] == "面试中"

    second = _schedule("candidate-flow", "课程试讲", 11)
    assert _feedback(second["interview_id"], "通过", "Offer")["status"] == "Offer待发"

    sent = client.post(
        "/api/v1/workflow/candidates/candidate-flow/offer", json={"status": "已发"},
    )
    accepted = client.post(
        "/api/v1/workflow/candidates/candidate-flow/offer", json={"status": "已接受"},
    )
    assert sent.status_code == 200
    assert accepted.json()["candidate"]["status"] == "Offer已接受"


def test_interview_can_be_rescheduled_and_cancelled(tmp_path, monkeypatch):
    from app.api import routes

    store = IntakeStore(str(tmp_path / "workflow.sqlite3"))
    _candidate(store, "candidate-calendar")
    monkeypatch.setattr(routes, "ops_store", store)
    monkeypatch.setattr(routes, "feishu_workflow", LocalCalendar())
    interview = _schedule("candidate-calendar", "一面", 20)

    changed = client.patch(
        f"/api/v1/workflow/interviews/{interview['interview_id']}",
        json={
            "interviewer_ids": ["ou_interviewer"],
            "start_at": "2030-09-21T14:00:00+08:00",
            "end_at": "2030-09-21T15:00:00+08:00",
            "location": "会议室 A", "note": "已改期",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["interview"]["location"] == "会议室 A"

    cancelled = client.post(
        f"/api/v1/workflow/interviews/{interview['interview_id']}/cancel",
        json={"reason": "候选人时间冲突"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["interview"]["status"] == "已取消"
    assert cancelled.json()["candidate"]["status"] == "通过"


def test_state_machine_accepts_custom_rounds_and_manual_next_step():
    assert candidate_action_target("待复核", "pass") == "通过"
    assert expected_next_round([]) == "一面"
    assert expected_next_round([{"round_name": "业务面", "status": "通过"}]) == "下一轮面试"
    assert feedback_target("一面", "通过", "继续面试") == "面试中"
    assert feedback_target("一面", "通过", "Offer") == "Offer待发"
    assert offer_target("Offer待发", "已发") == "Offer已发"
    validate_schedule("通过", [], "课程试讲")
    validate_schedule(
        "面试中", [{"round_name": "课程试讲", "status": "通过"}], "负责人沟通",
    )
    try:
        feedback_target("终面", "通过")
    except InvalidTransition as error:
        assert "下一环节" in str(error)
    else:
        raise AssertionError("missing next step was accepted")
