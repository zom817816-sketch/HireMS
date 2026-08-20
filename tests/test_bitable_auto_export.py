from datetime import datetime

from fastapi.testclient import TestClient

from app.api.models import ScreeningResult
from app.main import app
from app.services.intake_store import IntakeStore


def test_bitable_sync_is_thresholded_and_idempotent(tmp_path, monkeypatch):
    from app.api import routes

    store = IntakeStore(str(tmp_path / "ops.sqlite3"))

    class Writer:
        calls = []

        @staticmethod
        def configured():
            return True

        def write_candidates(self, candidates, job_name):
            self.calls.append((candidates, job_name))
            return len(candidates)

    writer = Writer()
    monkeypatch.setattr(routes, "ops_store", store)
    monkeypatch.setattr(routes, "bitable_writer", writer)
    monkeypatch.setattr(routes.settings, "FEISHU_EXPORT_MIN_SCORE", 0.7)
    candidates = [
        {"id": "high", "name": "高分", "overall_score": 0.91},
        {"id": "low", "name": "低分", "overall_score": 0.69},
    ]

    first = routes._sync_candidates_to_bitable("query-1", candidates, "课程顾问")
    second = routes._sync_candidates_to_bitable("query-1", candidates, "课程顾问")

    assert first == {
        "status": "success", "eligible": 1, "exported": 1,
        "already_synced": 0, "min_score": 0.7,
    }
    assert second == {
        "status": "up_to_date", "eligible": 1, "exported": 0,
        "already_synced": 1, "min_score": 0.7,
    }
    assert len(writer.calls) == 1
    assert [candidate["id"] for candidate in writer.calls[0][0]] == ["high"]
    assert store.bitable_synced_candidate_ids("query-1") == {"high"}


def test_cached_screening_result_does_not_run_pipeline(monkeypatch):
    from app.api import routes

    cached = ScreeningResult(
        query_id="cached-query",
        query_text="招聘课程顾问",
        total_candidates=0,
        candidates=[],
        created_at=datetime.now(),
        recall_scope={"job_category": "销售", "lookback_days": 60},
        bitable_sync={"status": "up_to_date", "exported": 0},
    ).model_dump(mode="json")
    monkeypatch.setitem(routes.query_storage, "cached-query", {
        "id": "cached-query", "text": "招聘课程顾问", "metadata": {},
        "created_at": datetime.now(), "screening_result": cached,
    })

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("cached result unexpectedly reran the pipeline")

    monkeypatch.setattr(routes.retriever, "retrieve", should_not_run)
    response = TestClient(app).get("/api/v1/results/cached-query")

    assert response.status_code == 200
    assert response.json()["bitable_sync"]["status"] == "up_to_date"
    routes.query_storage.pop("cached-query", None)
