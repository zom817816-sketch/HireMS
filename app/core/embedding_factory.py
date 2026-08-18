"""
嵌入模型工厂

按 `EMBEDDING_PROVIDER` 配置创建嵌入模型实例：

- ``openai``（默认）：OpenAI 兼容远程 API（``langchain_openai.OpenAIEmbeddings``），
  需要 ``EMBEDDING_API_KEY`` / ``EMBEDDING_BASE_URL``，支持透传 ``dimensions``。
- ``local``：本地 sentence-transformers 轻量模型
  （``langchain_huggingface.HuggingFaceEmbeddings``），无需 API Key，
  默认模型 ``BAAI/bge-small-zh-v1.5``（512 维 / 约 95MB，CPU 可跑）。
  需先安装可选依赖：``pip install -r requirements-local.txt``。

两种实现都暴露 LangChain 的 ``embed_documents`` / ``embed_query`` 接口，
向量库与上层检索代码对此无感知。
"""
import os
from typing import Any, Optional, Tuple

from loguru import logger
from langchain_openai import OpenAIEmbeddings

from config.config import settings

PROVIDER_OPENAI = "openai"
PROVIDER_LOCAL = "local"


def create_embeddings(embedding_model: Optional[str] = None) -> Tuple[Any, Optional[int]]:
    """按配置创建嵌入模型实例。

    Args:
        embedding_model (str, optional): 覆盖配置的模型名。

    Returns:
        (embeddings, dimensions):
        - openai 后端：dimensions 为 settings.EMBEDDING_DIMENSIONS（可能为 None）
        - local 后端：dimensions 恒为 None（由模型自身决定，需要时用
          `probe_embedding_dimension` 探测）
    """
    provider = (getattr(settings, "EMBEDDING_PROVIDER", PROVIDER_OPENAI) or PROVIDER_OPENAI).strip().lower()
    if provider == PROVIDER_OPENAI:
        return _create_openai_embeddings(embedding_model), getattr(settings, "EMBEDDING_DIMENSIONS", None)
    if provider == PROVIDER_LOCAL:
        return _create_local_embeddings(embedding_model), None
    raise ValueError(
        f"未知的 EMBEDDING_PROVIDER: {provider!r}（可选值：openai / local）"
    )


def probe_embedding_dimension(embeddings: Any, sample_text: str = "embedding dimension probe") -> int:
    """通过嵌入一小段文本探测模型输出维度（供 Milvus 等需要显式维度的 schema 使用）。"""
    return len(embeddings.embed_query(sample_text))


def _create_openai_embeddings(embedding_model: Optional[str] = None):
    """创建 OpenAI 兼容 API 的嵌入模型（保留原有 key/base_url 回退逻辑）。"""
    model_name = embedding_model or settings.EMBEDDING_MODEL
    api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or settings.EMBEDDING_API_KEY
    base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL") or settings.EMBEDDING_BASE_URL

    if not api_key:
        raise ValueError(
            "EMBEDDING_API_KEY (or OPENAI_API_KEY) environment variable is not set"
        )

    embedding_kwargs = {
        "model": model_name,
        "openai_api_key": api_key,
        "openai_api_base": base_url,
    }
    dimensions = getattr(settings, "EMBEDDING_DIMENSIONS", None)
    if dimensions:
        embedding_kwargs["dimensions"] = dimensions

    embeddings = OpenAIEmbeddings(**embedding_kwargs)
    logger.info(
        f"Initialized OpenAIEmbeddings with model: {model_name}"
        + (f", dimensions: {dimensions}" if dimensions else "")
    )
    return embeddings


def _create_local_embeddings(embedding_model: Optional[str] = None):
    """创建本地 sentence-transformers 嵌入模型（无需网络与 API Key）。"""
    model_name = (
        embedding_model
        or settings.EMBEDDING_MODEL
        or getattr(settings, "EMBEDDING_DEFAULT_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5")
    )
    device = getattr(settings, "EMBEDDING_DEVICE", "cpu") or "cpu"

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as e:  # pragma: no cover - 给出清晰的安装提示
        raise ImportError(
            "EMBEDDING_PROVIDER=local 需要本地嵌入依赖，请先安装："
            "pip install -r requirements-local.txt"
            "（即 pip install langchain-huggingface sentence-transformers）"
        ) from e

    embedding_kwargs = {
        "model_name": model_name,
        # 余弦相似度检索场景统一做 L2 归一化
        "encode_kwargs": {"normalize_embeddings": True},
        "model_kwargs": {"device": device},
    }
    cache_folder = getattr(settings, "LOCAL_EMBEDDING_CACHE_DIR", "")
    if cache_folder:
        embedding_kwargs["cache_folder"] = cache_folder
    query_instruction = getattr(settings, "EMBEDDING_QUERY_INSTRUCTION", "")
    if query_instruction:
        # bge 等检索模型推荐为查询侧加指令前缀（文档侧不加）
        embedding_kwargs["query_instruction"] = query_instruction

    embeddings = HuggingFaceEmbeddings(**embedding_kwargs)
    logger.info(
        f"Initialized local HuggingFaceEmbeddings with model: {model_name}, device: {device}"
        + (f", cache: {cache_folder}" if cache_folder else "")
        + (f", query_instruction: {query_instruction!r}" if query_instruction else "")
        + " (dimension auto-detected)"
    )
    return embeddings
