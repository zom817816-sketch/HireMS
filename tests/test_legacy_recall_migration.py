from unittest.mock import MagicMock, patch

from app.api import routes
from app.services.intake_store import IntakeStore


def test_legacy_resume_metadata_is_backfilled_without_llm(tmp_path):
    store = IntakeStore(str(tmp_path / "ops.sqlite3"))
    store.record_resume_identity(
        "legacy-sales", "legacy-fp", "", "legacy@example.com", "张三", "张三", "legacy.md",
    )
    retriever = MagicMock()
    retriever.get_indexed_resume.return_value = {
        "id": "legacy-sales",
        "text": "曾任K12课程顾问，负责客户邀约、转化和签单",
        "metadata": {"name": "张三", "work_experience": [{"title": "课程顾问"}]},
    }

    with (
        patch.object(routes, "ops_store", store),
        patch.object(routes, "retriever", retriever),
        patch.object(routes, "_legacy_recall_migration_done", False),
    ):
        upgraded = routes._upgrade_legacy_recall_metadata()
        second_run = routes._upgrade_legacy_recall_metadata()

    identity = store.find_resume_by_fingerprint("legacy-fp")
    assert upgraded == 1
    assert second_run == 0
    assert identity["job_category"] == "销售"
    assert identity["job_category_version"] == 2
    assert identity["imported_at_epoch"] > 0
    indexed_metadata = retriever.add_resume.call_args.args[2]
    assert indexed_metadata["job_category"] == "销售"
    assert indexed_metadata["imported_at"]
