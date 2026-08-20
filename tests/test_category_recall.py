from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.core.milvus_store import _milvus_filter_from_where
from app.core.retriever import Retriever
from app.models.metadata import QueryMetadata


def test_retriever_limits_recall_to_recent_matching_category():
    now = datetime.now(timezone.utc)
    recent = int((now - timedelta(days=5)).timestamp())
    old = int((now - timedelta(days=90)).timestamp())
    vector_store = MagicMock()
    vector_store.query_collection.return_value = {
        "ids": [["recent-sales", "old-sales", "recent-teacher"]],
        "documents": [["课程顾问", "课程顾问", "数学老师"]],
        "metadatas": [[
            {"name": "A", "job_category": "销售", "imported_at_epoch": recent},
            {"name": "B", "job_category": "销售", "imported_at_epoch": old},
            {"name": "C", "job_category": "教师", "imported_at_epoch": recent},
        ]],
        "distances": [[0.1, 0.2, 0.3]],
    }

    results = Retriever(vector_store).retrieve(
        QueryMetadata(keywords=["课程顾问"], job_category="销售"),
        n_results=20,
        lookback_days=60,
    )

    assert [item["id"] for item in results] == ["recent-sales"]
    where = vector_store.query_collection.call_args.kwargs["where"]
    assert where["$and"][0] == {"job_category": {"$eq": "销售"}}
    cutoff = where["$and"][1]["imported_at_epoch"]["$gte"]
    assert int((now - timedelta(days=61)).timestamp()) < cutoff < int(now.timestamp())


def test_milvus_filter_translation_targets_json_metadata():
    expression = _milvus_filter_from_where({"$and": [
        {"job_category": {"$eq": "教师"}},
        {"imported_at_epoch": {"$gte": 123}},
    ]})

    assert 'metadata["job_category"] == "教师"' in expression
    assert 'metadata["imported_at_epoch"] >= 123' in expression
