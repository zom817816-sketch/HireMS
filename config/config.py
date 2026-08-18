"""
集中式配置模块

所有运行时配置统一从环境变量读取（支持 .env 文件）。其他模块可通过
`from config.config import settings` 获取配置默认值。

支持为 LLM（对话）与 Embedding（向量化）分别配置 key / base_url / model，
并向后兼容统一的 OPENAI_API_KEY / OPENAI_BASE_URL。
"""
import os

# 加载 .env 文件（如果安装了 python-dotenv 且存在 .env）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv 未安装时静默跳过
    pass


def _first_env(*names: str, default: str = "") -> str:
    """按顺序返回第一个非空环境变量值，全部为空时返回 default。"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


class Settings:
    """应用配置项"""

    # 兼容旧的统一配置（作为 LLM / Embedding 的回退）
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

    # ---- LLM（对话）----
    # 优先 LLM_*，回退到 OPENAI_*
    LLM_API_KEY: str = _first_env("LLM_API_KEY", "OPENAI_API_KEY")
    LLM_BASE_URL = _first_env("LLM_BASE_URL", "OPENAI_BASE_URL") or None
    # 兼容用户可能写成 LL_MODEL 的拼写
    LLM_MODEL: str = _first_env("LLM_MODEL", "LL_MODEL", default="gpt-4o")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))

    # ---- Embedding（向量化）----
    # 优先 EMBEDDING_*，回退到 LLM_* / OPENAI_*
    EMBEDDING_API_KEY: str = _first_env("EMBEDDING_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")
    EMBEDDING_BASE_URL = _first_env("EMBEDDING_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL") or None
    # 嵌入后端：openai（默认，OpenAI 兼容 API）/ local（本地 sentence-transformers 轻量模型）
    EMBEDDING_PROVIDER: str = (os.getenv("EMBEDDING_PROVIDER") or "openai").strip().lower()
    # local 后端的默认模型（512 维 / 约 95MB，中文检索效果好的轻量级选择）
    EMBEDDING_DEFAULT_LOCAL_MODEL: str = "BAAI/bge-small-zh-v1.5"
    # 模型名：未显式配置时按后端选择默认值
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL") or (
        EMBEDDING_DEFAULT_LOCAL_MODEL if EMBEDDING_PROVIDER == "local" else "text-embedding-3-small"
    )
    # local 后端运行设备（cpu / cuda / mps，传给 sentence-transformers）
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    # local 后端可选：检索查询前缀指令（bge 中文系列推荐
    # “为这个句子生成表示以用于检索相关文章：”），留空则不加
    EMBEDDING_QUERY_INSTRUCTION: str = os.getenv("EMBEDDING_QUERY_INSTRUCTION", "")
    # 嵌入向量维度（仅 openai 后端使用；智谱 embedding-3 支持自定义，默认 2048。
    # local 后端维度由模型自动决定，此配置被忽略）
    EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS")) if os.getenv("EMBEDDING_DIMENSIONS") else 2048

    # 向量数据库
    # 选择向量库后端：chroma（默认，本地持久化）/ milvus（Milvus/Zilliz Cloud）
    VECTOR_DB: str = os.getenv("VECTOR_DB", "chroma").lower()
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")

    # ---- Milvus / Zilliz Cloud（当 VECTOR_DB=milvus 时生效）----
    # 优先使用完整 URI；若只提供 HOST/PORT 则自动拼接
    _MILVUS_HOST = os.getenv("MILVUS_HOST", "")
    _MILVUS_PORT = os.getenv("MILVUS_PORT", "")

    @staticmethod
    def _build_milvus_uri(uri: str, host: str, port: str) -> str:
        if uri:
            return uri
        if not host:
            return ""
        # host 已带协议（http/https）时按需补端口；否则按 host:port 处理
        if host.startswith("http://") or host.startswith("https://"):
            if port and port not in ("80", "443"):
                return f"{host}:{port}"
            return host
        if port:
            return f"http://{host}:{port}"
        return host

    MILVUS_URI: str = _build_milvus_uri(
        os.getenv("MILVUS_URI", ""), _MILVUS_HOST, _MILVUS_PORT
    )
    MILVUS_TOKEN: str = os.getenv("MILVUS_TOKEN", "")
    # 简历专用集合名（避免污染其他业务集合，如 customer_service_kb）
    MILVUS_COLLECTION: str = os.getenv("MILVUS_COLLECTION", "resume_screening")
    MILVUS_INDEX: str = os.getenv("MILVUS_INDEX") or os.getenv("INDEX") or "AUTOINDEX"

    # ---- 服务/跨域 ----
    # 允许的前端来源（逗号分隔）；为空时允许所有来源
    _ALLOWED_ORIGINS_RAW = os.getenv("SERVER_ALLOWED_ORIGINS", "")
    SERVER_ALLOWED_ORIGINS = (
        [o.strip() for o in _ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
        if _ALLOWED_ORIGINS_RAW
        else ["*"]
    )

    # 缓存
    CACHE_DIR: str = os.getenv("CACHE_DIR", "./cache")

    # 日志级别
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ---- 飞书邮箱 / 多维表格 ----
    MAIL_IMAP_HOST: str = os.getenv("MAIL_IMAP_HOST", "")
    MAIL_IMAP_PORT: int = int(os.getenv("MAIL_IMAP_PORT", "993"))
    MAIL_IMAP_USER: str = os.getenv("MAIL_IMAP_USER", "")
    MAIL_IMAP_PASSWORD: str = os.getenv("MAIL_IMAP_PASSWORD", "")
    MAIL_IMAP_FOLDER: str = os.getenv("MAIL_IMAP_FOLDER", "INBOX")
    MAIL_SUBJECT_KEYWORDS: str = os.getenv("MAIL_SUBJECT_KEYWORDS", "简历,应聘,求职")
    MAIL_LOOKBACK_DAYS: int = int(os.getenv("MAIL_LOOKBACK_DAYS", "7"))

    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    FEISHU_BITABLE_APP_TOKEN: str = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
    FEISHU_BITABLE_TABLE_ID: str = os.getenv("FEISHU_BITABLE_TABLE_ID", "")
    FEISHU_EXPORT_MIN_SCORE: float = float(os.getenv("FEISHU_EXPORT_MIN_SCORE", "0.70"))


settings = Settings()
