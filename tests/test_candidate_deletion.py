from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.intake_store import IntakeStore


client = TestClient(app)


def test_intake_store_deletes_candidate_related_records(tmp_path):
    store = IntakeStore(str(tmp_path / "operations.sqlite3"))
    candidate = {"id": "candidate-delete", "name": "测试候选人", "overall_score": 0.8}
    store.upsert_candidate(candidate, "测试岗位")
    store.create_interview({
        "interview_id": "interview-delete", "candidate_id": candidate["id"], "round_name": "一面",
        "interviewer_ids": [], "start_at": "2026-01-01T10:00:00", "end_at": "2026-01-01T11:00:00",
    })
    store.notification(candidate["id"], "new_candidate", "local", "success", "test")

    cleanup = store.delete_candidate(candidate["id"])

    assert cleanup == {"interviews": 1, "notifications": 1}
    assert store.get_candidate(candidate["id"]) is None
    assert store.list_interviews(candidate["id"]) == []


@patch("app.api.routes.resume_storage", {"candidate-delete": {"id": "candidate-delete"}})
@patch("app.api.routes.retriever")
@patch("app.api.routes.ops_store")
def test_delete_candidate_api_removes_vector_and_local_records(mock_store, mock_retriever):
    mock_store.get_candidate.return_value = {"id": "candidate-delete", "name": "测试候选人"}
    mock_store.list_interviews.return_value = []
    mock_store.delete_candidate.return_value = {"interviews": 0, "notifications": 2}

    response = client.delete("/api/v1/workflow/candidates/candidate-delete")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    mock_retriever.remove_resume.assert_called_once_with("candidate-delete")
    mock_store.delete_candidate.assert_called_once_with("candidate-delete")


@patch("app.api.routes.retriever")
@patch("app.api.routes.ops_store")
def test_delete_candidate_api_blocks_synced_calendar_event(mock_store, mock_retriever):
    mock_store.get_candidate.return_value = {"id": "candidate-delete", "name": "测试候选人"}
    mock_store.list_interviews.return_value = [{"calendar_event_id": "evt_123"}]

    response = client.delete("/api/v1/workflow/candidates/candidate-delete")

    assert response.status_code == 409
    assert "飞书日历" in response.json()["detail"]
    mock_retriever.remove_resume.assert_not_called()
