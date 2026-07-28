"""Configuration management for the knowledge graph system."""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "12345678"

    # ChromaDB
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000

    # SQLite
    SQLITE_PATH: str = "./data/sqlite/app.db"

    # API Keys
    SILICON_FLOW_API_KEY: str = ""
    SILICON_FLOW_BASE_URL: str = "https://api.siliconflow.cn/v1"
    KIMI_API_KEY: str = ""
    KIMI_BASE_URL: str = "https://api.moonshot.cn/v1"

    # Bailian (百炼) LLM - 使用 OpenAI 兼容模式
    BAILIAN_API_KEY: str = ""
    BAILIAN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    BAILIAN_MODEL: str = "qwen3.7-flash"

    # LLM Settings
    LLM_MODEL_KIMI: str = "kimi-k2-0905-preview"
    LLM_MODEL_SILICON: str = "Qwen/Qwen3-8B-Instruct"

    # JWT
    JWT_SECRET: str = "your-secret-key-change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Upload
    UPLOAD_DIR: str = "./data/uploads"
    MAX_FILE_SIZE: int = 10485760  # 10MB

    # Embedding
    EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-8B"
    EMBEDDING_DIM: int = 1024

    # Rerank
    RERANK_MODEL: str = "Qwen/Qwen3-Reranker-8B"

    # Retrieval latency tuning
    # Query rewriting costs a full LLM round-trip that BLOCKS retrieval, so
    # it is only worth paying for longer queries. Queries shorter than this
    # (stripped char count) skip the rewrite entirely. Set to 0 to always
    # rewrite (old behavior), or to a very large number to never rewrite.
    QUERY_REWRITE_MIN_LEN: int = 20
    # Candidates each retriever (vector + BM25) feeds into RRF and then the
    # reranker. The reranker only needs enough candidates to reliably contain
    # the final top_k; 25 roughly halves rerank payload/latency vs 50 with no
    # measurable hit to top-5 quality.
    RERANK_RECALL_K: int = 25

    # Entity Extraction
    ENABLE_LLM_EXTRACTION: bool = True
    USE_RULE_EXTRACTION: bool = False  # 纯 LLM 模式，不使用规则提取（更快）
    ENTITY_BATCH_SIZE: int = 200  # 实体提取批次大小
    ENTITY_EXTRACTION_DELAY: float = 0
    # Concurrent in-flight LLM extraction requests. The old hard-coded 50
    # regularly tripped provider rate limits (429s), and each 429 paid a
    # 2–4s backoff — so a lower ceiling sustains HIGHER effective
    # throughput. 20 keeps ~20 chunks extracting in parallel without
    # provoking 429 storms on the Bailian compatible-mode tier.
    LLM_EXTRACTION_CONCURRENCY: int = 20
    # max_tokens for extraction calls. Entity/relation JSON for a 2000-char
    # chunk fits comfortably under 1024 tokens; the old 8000 default made
    # the provider reserve/allocate far more generation budget than any
    # extraction response could ever use.
    LLM_EXTRACT_MAX_TOKENS: int = 1024

    # CORS - comma-separated list of allowed origins (no wildcards with credentials)
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    # App environment: "development" (default) or "production"
    APP_ENV: str = "development"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


# Publicly-known placeholder secrets that must never be used at runtime. Both
# the code default and the .env.example placeholder are public, so accepting
# either would let anyone forge valid JWTs for any user (full auth bypass).
_INSECURE_JWT_SECRETS = {
    "",
    "your-secret-key-change-this-in-production",
    "replace-me-with-a-strong-random-value",
}


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.

    Refuses to start with a known placeholder JWT_SECRET in ANY environment —
    the placeholders are public and would allow trivial authentication bypass.
    Generate one with:
        python -c "import secrets; print(secrets.token_urlsafe(48))"
    """
    settings = Settings()
    if settings.JWT_SECRET in _INSECURE_JWT_SECRETS:
        raise RuntimeError(
            "JWT_SECRET is set to an insecure placeholder. Generate a strong "
            "random value via `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"` and set it with the JWT_SECRET "
            "environment variable."
        )
    return settings
