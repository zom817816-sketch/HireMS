from unittest.mock import MagicMock, patch

from app.api import routes
from app.core.deduplication import (
    normalize_email, normalize_name, normalize_phone, resume_fingerprint,
)
from app.core.retriever import Retriever
from app.models.metadata import ResumeMetadata
from app.services.intake_store import IntakeStore


def test_normalized_identity_and_content_fingerprint():
    assert resume_fingerprint("张三\nPython") == resume_fingerprint(" 张三  Python ")
    assert normalize_phone("+86 138-0000-1234") == "13800001234"
    assert normalize_email(" USER@Example.COM ") == "user@example.com"
    assert normalize_name("张 三") == "张三"


def test_identity_store_tracks_content_identity_and_same_name(tmp_path):
    store = IntakeStore(str(tmp_path / "dedup.sqlite3"))
    store.record_resume_identity(
        "resume-1", "fp-1", "13800000000", "one@example.com", "张三", "张三", "one.md",
    )

    assert store.find_resume_by_fingerprint("fp-1")["resume_id"] == "resume-1"
    assert store.find_resume_by_identity(phone_key="13800000000")["resume_id"] == "resume-1"
    assert store.find_resume_by_identity(email_key="one@example.com")["resume_id"] == "resume-1"
    assert store.find_resumes_by_name("张三") == [
        {"resume_id": "resume-1", "name": "张三", "filename": "one.md"}
    ]


def test_ingestion_skips_exact_duplicate_before_second_llm_call(tmp_path):
    store = IntakeStore(str(tmp_path / "dedup.sqlite3"))
    metadata = ResumeMetadata(name="张三", email="one@example.com", phone="13800000000")

    with (
        patch.object(routes, "ops_store", store),
        patch.object(routes, "resume_storage", {}),
        patch.object(routes, "_extract_resume_text", return_value="张三 Python 工程师"),
        patch.object(routes.metadata_extractor, "extract_metadata", return_value=metadata) as extract,
        patch.object(routes.retriever, "add_resume") as add_resume,
    ):
        first = routes._ingest_resume("one.md", b"first")
        second = routes._ingest_resume("copy.md", b"second")

    assert first["status"] == "created"
    assert second == {
        "resume_id": first["resume_id"], "status": "duplicate",
        "name": "张三", "possible_duplicate": False,
    }
    assert extract.call_count == 1
    assert add_resume.call_count == 1


def test_ingestion_updates_same_email_with_stable_resume_id(tmp_path):
    store = IntakeStore(str(tmp_path / "dedup.sqlite3"))
    metadata = ResumeMetadata(name="张三", email="ONE@example.com")

    with (
        patch.object(routes, "ops_store", store),
        patch.object(routes, "resume_storage", {}),
        patch.object(routes, "_extract_resume_text", side_effect=["第一版简历", "第二版简历"]),
        patch.object(routes.metadata_extractor, "extract_metadata", return_value=metadata),
        patch.object(routes.retriever, "add_resume") as add_resume,
    ):
        first = routes._ingest_resume("v1.md", b"v1")
        second = routes._ingest_resume("v2.md", b"v2")

    assert first["status"] == "created"
    assert second["status"] == "updated"
    assert second["resume_id"] == first["resume_id"]
    assert [call.args[0] for call in add_resume.call_args_list] == [first["resume_id"], first["resume_id"]]


def test_retrieval_hides_legacy_duplicates_by_email_or_content():
    retriever = Retriever(MagicMock())
    results = {
        "ids": [["old-1", "old-2", "different"]],
        "documents": [["同一份简历", "格式不同但邮箱相同", "另一份简历"]],
        "metadatas": [[
            {"name": "张三", "email": "same@example.com"},
            {"name": "张三", "email": "SAME@example.com"},
            {"name": "李四", "email": "other@example.com"},
        ]],
        "distances": [[0.1, 0.2, 0.3]],
    }

    formatted = retriever._format_results(results)

    assert [item["id"] for item in formatted] == ["old-1", "different"]
