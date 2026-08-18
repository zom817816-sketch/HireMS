"""
embedding_factory 单元测试

覆盖 openai / local 两种嵌入后端的构建逻辑，全部使用假对象，不联网、
不加载真实 sentence-transformers 模型。
"""
import sys
import types

import pytest

from app.core import embedding_factory
from config.config import settings


class _StubEmbeddings:
    """记录构造参数的假嵌入模型。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


# ---------------------------------------------------------------- #
# openai 后端
# ---------------------------------------------------------------- #
def test_openai_provider_builds_openai_embeddings(monkeypatch):
    created = {}

    def _fake_openai(**kwargs):
        emb = _StubEmbeddings(**kwargs)
        created["emb"] = emb
        return emb

    monkeypatch.setattr(embedding_factory, "OpenAIEmbeddings", _fake_openai)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 1024)

    embeddings, dims = embedding_factory.create_embeddings()

    assert created["emb"] is embeddings
    assert embeddings.kwargs["model"] == "test-model"
    assert embeddings.kwargs["openai_api_key"]  # 回退到测试占位 key
    assert embeddings.kwargs["dimensions"] == 1024
    assert dims == 1024


def test_openai_provider_without_dimensions_param(monkeypatch):
    """EMBEDDING_DIMENSIONS 未配置（None）时不透传 dimensions。"""
    monkeypatch.setattr(embedding_factory, "OpenAIEmbeddings", _StubEmbeddings)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", None)

    embeddings, dims = embedding_factory.create_embeddings()
    assert "dimensions" not in embeddings.kwargs
    assert dims is None


def test_openai_provider_missing_api_key(monkeypatch):
    monkeypatch.setattr(embedding_factory, "OpenAIEmbeddings", _StubEmbeddings)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")

    with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
        embedding_factory.create_embeddings()


# ---------------------------------------------------------------- #
# local 后端
# ---------------------------------------------------------------- #
def test_local_provider_builds_local_embeddings(monkeypatch):
    created = {}

    class _FakeHuggingFaceEmbeddings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created["emb"] = self

        def embed_query(self, text):
            return [0.0] * 512

    fake_module = types.ModuleType("langchain_huggingface")
    fake_module.HuggingFaceEmbeddings = _FakeHuggingFaceEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_huggingface", fake_module)

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    monkeypatch.setattr(settings, "EMBEDDING_DEVICE", "cpu")
    monkeypatch.setattr(settings, "EMBEDDING_QUERY_INSTRUCTION", "为这个句子生成表示以用于检索相关文章：")

    embeddings, dims = embedding_factory.create_embeddings()

    assert created["emb"] is embeddings
    assert embeddings.kwargs["model_name"] == "BAAI/bge-small-zh-v1.5"
    assert embeddings.kwargs["encode_kwargs"] == {"normalize_embeddings": True}
    assert embeddings.kwargs["model_kwargs"] == {"device": "cpu"}
    assert embeddings.kwargs["query_instruction"]
    # local 后端维度由模型决定，工厂不返回固定值
    assert dims is None
    assert embedding_factory.probe_embedding_dimension(embeddings) == 512


def test_local_provider_without_query_instruction(monkeypatch):
    """未配置查询前缀时不传 query_instruction 参数。"""
    created = {}

    class _FakeHuggingFaceEmbeddings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created["emb"] = self

    fake_module = types.ModuleType("langchain_huggingface")
    fake_module.HuggingFaceEmbeddings = _FakeHuggingFaceEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_huggingface", fake_module)

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "")
    monkeypatch.setattr(settings, "EMBEDDING_QUERY_INSTRUCTION", "")

    embeddings, _ = embedding_factory.create_embeddings()
    # EMBEDDING_MODEL 为空时回退到本地默认模型
    assert embeddings.kwargs["model_name"] == "BAAI/bge-small-zh-v1.5"
    assert "query_instruction" not in embeddings.kwargs


def test_local_provider_missing_dependency(monkeypatch):
    """缺少 langchain-huggingface 依赖时给出含安装提示的 ImportError。"""
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "langchain_huggingface":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")

    with pytest.raises(ImportError, match="requirements-local"):
        embedding_factory.create_embeddings()


# ---------------------------------------------------------------- #
# 其他
# ---------------------------------------------------------------- #
def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        embedding_factory.create_embeddings()


def test_probe_embedding_dimension():
    class _FixedDimEmbeddings:
        def embed_query(self, text):
            return [0.1, 0.2, 0.3]

    assert embedding_factory.probe_embedding_dimension(_FixedDimEmbeddings()) == 3
