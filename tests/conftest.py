"""
pytest 公共配置

在导入应用模块之前注入测试用的环境变量，避免 `app.api.routes` 在模块加载时
实例化 LLMClient / VectorStoreManager 因缺少 OPENAI_API_KEY 而报错。
这些是占位值，构造客户端时不会发起网络请求。
"""
import os
import hashlib
import tempfile
from pathlib import Path


# Application services are created while test modules are imported. Point every
# persistent local store at a per-test-session directory before that happens;
# otherwise API fixture candidates (for example candidate_001 / 张三) would be
# written into the developer's real data/hirems_ops.sqlite3 and shown by the UI.
_TEST_RUNTIME_DIR = tempfile.TemporaryDirectory(prefix="hirems-tests-")
_TEST_RUNTIME_PATH = Path(_TEST_RUNTIME_DIR.name)
os.environ["HIREMS_OPS_DB_PATH"] = str(_TEST_RUNTIME_PATH / "hirems_ops.sqlite3")
os.environ["RESUME_FILE_DIR"] = str(_TEST_RUNTIME_PATH / "resumes")

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://test.url/v1")
# 单元测试固定使用 ChromaDB 后端与 openai 嵌入后端（FakeEmbeddings 按此路径注入）
os.environ["VECTOR_DB"] = "chroma"
os.environ["EMBEDDING_PROVIDER"] = "openai"

import pytest


class FakeEmbeddings:
    """确定性假嵌入模型，避免测试发起真实网络请求。

    根据文本内容生成稳定的 8 维向量，相同文本得到相同向量。
    """

    DIM = 8

    def __init__(self, *args, **kwargs):
        # 兼容 OpenAIEmbeddings(model=..., openai_api_key=..., openai_api_base=...)
        pass

    def _embed(self, text: str):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 取前 DIM 个字节归一化到 [0, 1)
        return [digest[i] / 255.0 for i in range(self.DIM)]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """全局替换嵌入工厂使用的 OpenAIEmbeddings，避免真实网络调用。"""
    monkeypatch.setattr(
        "app.core.embedding_factory.OpenAIEmbeddings",
        FakeEmbeddings,
        raising=False,
    )
    yield
