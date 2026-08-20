from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.services.intake_store import IntakeStore
from app.services.resume_file_store import ResumeFileStore


client = TestClient(app)


def test_resume_original_survives_memory_restart_and_is_downloadable(tmp_path):
    store = IntakeStore(str(tmp_path / "ops.sqlite3"))
    files = ResumeFileStore(tmp_path / "resumes")
    stored = files.save("resume_123", "candidate.md", b"# Candidate\nPython")
    store.record_resume_file(
        stored["resume_id"], stored["original_filename"], stored["relative_path"],
        stored["media_type"], stored["size_bytes"],
    )

    with (
        patch.object(routes, "ops_store", store),
        patch.object(routes, "resume_file_store", files),
        patch.object(routes, "resume_storage", {}),
    ):
        response = client.get("/api/v1/resumes/resume_123/file")

    assert response.status_code == 200
    assert response.content == b"# Candidate\nPython"
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"].startswith("inline")


def test_resume_original_missing_returns_actionable_404(tmp_path):
    store = IntakeStore(str(tmp_path / "ops.sqlite3"))
    files = ResumeFileStore(tmp_path / "resumes")

    with patch.object(routes, "ops_store", store), patch.object(routes, "resume_file_store", files):
        response = client.get("/api/v1/resumes/legacy_candidate/file")

    assert response.status_code == 404
    assert "重新导入" in response.json()["detail"]


def test_resume_file_store_rejects_path_escape(tmp_path):
    files = ResumeFileStore(tmp_path / "resumes")

    with pytest.raises(ValueError, match="escapes storage directory"):
        files.resolve("../outside.md")


def test_resume_file_record_and_file_can_be_deleted(tmp_path):
    store = IntakeStore(str(tmp_path / "ops.sqlite3"))
    files = ResumeFileStore(tmp_path / "resumes")
    stored = files.save("resume_delete", "candidate.txt", b"resume")
    store.record_resume_file(
        stored["resume_id"], stored["original_filename"], stored["relative_path"],
        stored["media_type"], stored["size_bytes"],
    )

    assert store.get_resume_file("resume_delete") is not None
    assert files.delete(stored["relative_path"]) is True
    store.delete_resume_file("resume_delete")
    assert store.get_resume_file("resume_delete") is None
