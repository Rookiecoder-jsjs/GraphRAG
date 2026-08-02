"""Configuration management for the knowledge graph system."""
import os
from functools import lru_cache
from pathlib import Path
from pydantic import AliasChoices, Field
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

    # Separate, scoped API key for the multimodal embedding model
    # (qwen3-vl-embedding). Same provider (DashScope), but typically a
    # different key with its own quota — share with the LLM key only if
    # you don't mind coupling their limits. Both spellings are accepted:
    # the canonical BAILIAN_API_KEY_QWEN_VL_EMBEDDING and the legacy
    # BAILIAN_API_KEY_Qwen-VL-Embedding the user set first.
    BAILIAN_API_KEY_QWEN_VL_EMBEDDING: str = Field(
        default="",
        validation_alias=AliasChoices(
            "BAILIAN_API_KEY_QWEN_VL_EMBEDDING",
            "BAILIAN_API_KEY_Qwen-VL-Embedding",
        ),
    )

    # LLM Settings
    LLM_MODEL_KIMI: str = "kimi-k2-0905-preview"
    LLM_MODEL_SILICON: str = "Qwen/Qwen3-8B-Instruct"

    # Provider selection for LLM / reranker — keys into the provider
    # registry (app/services/providers/). Mirrors EMBEDDING_PROVIDER: a
    # hard switch, not a failover; invalid values are rejected at startup.
    LLM_PROVIDER: str = "bailian"
    RERANKER_PROVIDER: str = "siliconflow"

    # JWT
    JWT_SECRET: str = "your-secret-key-change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Upload
    UPLOAD_DIR: str = "./data/uploads"
    MAX_FILE_SIZE: int = 10485760  # 10MB

    # Root directory for uploaded/extracted images. Defaults to the
    # repo-root data dir (D:\NC\data\images) anchored on THIS FILE's
    # location — independent of the process CWD, unlike UPLOAD_DIR which
    # is relative to the backend working dir. Images live outside
    # backend/ because they are first-class retrieval units: the LLM reads
    # them back from disk when they win retrieval (multimodal
    # attachments), and co-locating them with the other data stores
    # (sqlite/chromadb/neo4j) gives one backup story. SQLite stores the
    # RELATIVE path "images/<doc_id>/<file>", resolved against this root —
    # moving the root is a folder move, no DB rewrite. Override with an
    # absolute path, or a relative path (resolved against the CWD).
    IMAGE_DIR: str = Field(
        default_factory=lambda: str(
            Path(__file__).resolve().parents[2] / "data" / "images"
        )
    )

    # Image uploads / multimodal retrieval
    # 8 MB (not 10): images are sent to the embedding API as base64 data
    # URIs, whose ~33% inflation would push a 10 MB image past typical
    # request-body limits.
    IMAGE_MAX_FILE_SIZE: int = 8388608
    # Max image hits appended after the reranked text results — images
    # bypass the text-only reranker and keep cosine order.
    IMAGE_RESULT_QUOTA: int = 2
    # Images whose cosine similarity to the query reaches this threshold
    # are promoted AHEAD of text results in search — the multimodal
    # model's own relevance verdict, which the text-only reranker cannot
    # see (rerank scores live on a compressed ~0.00x scale and would
    # bury a genuinely relevant image under unrelated texts). Below the
    # threshold images keep the text-first ordering. Measured against
    # real corpus (2026-08-02): relevant images 0.49–0.56, irrelevant
    # images ≤ 0.41 — 0.45 splits cleanly.
    IMAGE_PROMOTION_THRESHOLD: float = 0.45
    # Extracted images smaller than this on BOTH axes are skipped
    # (icons, bullets, decoration — Phase 2b).
    IMAGE_MIN_DIMENSION: int = 120

    # Embedding
    # Provider selection: "siliconflow" (OpenAI-compatible /embeddings) or
    # "dashscope" (native multimodal endpoint — text AND images). This is a
    # HARD switch, NOT a failover: the two providers produce vectors in
    # incompatible semantic spaces, so switching providers REQUIRES re-running
    # scripts/migrate_embeddings.py with the backend stopped. An invalid value
    # is rejected at startup (see get_settings).
    EMBEDDING_PROVIDER: str = "dashscope"
    # SiliconFlow provider (OpenAI-compatible) model name.
    EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-8B"
    # DashScope provider (native multimodal endpoint; auth via BAILIAN_API_KEY).
    # Verified by scripts/probe_vl_embedding.py (2026-08-02): the compat-mode
    # endpoint 404s for this model — only the native endpoint works.
    DASHSCOPE_EMBEDDING_MODEL: str = "qwen3-vl-embedding"
    DASHSCOPE_EMBEDDING_URL: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    # Authoritative pinned output dimension for EVERY provider: dashscope
    # requests it via MRL (parameters.dimension), and zero-padding of blank
    # text must match it. Changing this requires a full re-embed.
    EMBEDDING_DIM: int = 1024

    # Rerank
    RERANK_MODEL: str = "Qwen/Qwen3-Reranker-8B"

    # Retrieval pipeline composition. Steps are keys into the retrieval
    # step registry (app/services/retrieval/steps.py); an unknown name
    # fails at startup (main.py lifespan validation). Chat runs the full
    # hybrid chain (graph_retrieve self-skips unless use_graph_rag is
    # set); search is vector-only + image promotion by design.
    CHAT_PIPELINE: str = (
        "query_rewrite,graph_retrieve,query_embed,bm25_retrieve,"
        "vector_retrieve,rrf_fuse,rerank,context_enrich,entity_enrich"
    )
    SEARCH_PIPELINE: str = (
        "query_embed,vector_retrieve,rerank,image_promote,"
        "context_enrich,entity_enrich"
    )

    # Graph-RAG fusion strategy. True: graph candidate chunks enter RRF
    # as a third lane, competing fairly with vector + BM25. False: the
    # legacy behavior — graph hits are prepended ahead of the fused set
    # (operator rollback without code changes).
    GRAPH_RRF_LANE: bool = True
    # When the graph lane is active, merge query rewrite + entity
    # extraction into ONE LLM call (halves the blocking LLM latency of
    # the old two-call flow). False restores the exact two-call behavior.
    CHAT_COMBINED_REWRITE_EXTRACT: bool = True

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

    # Load backend/.env FIRST (so it can override the repo-root .env for
    # backend-specific keys like SQLITE_PATH or UPLOAD_DIR), then fall back
    # to the repo-root .env for keys the user only set there — most
    # importantly the dedicated VL-embedding key, which lives at the root
    # by convention and would otherwise silently fall back to the LLM key.
    class Config:
        env_file = [".env", "../.env"]
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

# Valid EMBEDDING_PROVIDER values. A typo here (e.g. "dash_scope") would
# otherwise silently land on the siliconflow branch and embed with the wrong
# model — or worse, mix vector spaces after a partial switch.
_EMBEDDING_PROVIDERS = {"siliconflow", "dashscope"}

# Valid LLM / reranker provider values — keys into the provider registry
# (app/services/providers/). Single-provider today; the list grows when a
# second vendor is registered.
_LLM_PROVIDERS = {"bailian"}
_RERANKER_PROVIDERS = {"siliconflow"}


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
    if settings.EMBEDDING_PROVIDER.lower() not in _EMBEDDING_PROVIDERS:
        raise RuntimeError(
            f"EMBEDDING_PROVIDER={settings.EMBEDDING_PROVIDER!r} is not valid; "
            f"expected one of {sorted(_EMBEDDING_PROVIDERS)}. Switching providers "
            "changes the vector space — re-run scripts/migrate_embeddings.py "
            "with the backend stopped."
        )
    if settings.LLM_PROVIDER.lower() not in _LLM_PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER={settings.LLM_PROVIDER!r} is not valid; "
            f"expected one of {sorted(_LLM_PROVIDERS)}"
        )
    if settings.RERANKER_PROVIDER.lower() not in _RERANKER_PROVIDERS:
        raise RuntimeError(
            f"RERANKER_PROVIDER={settings.RERANKER_PROVIDER!r} is not valid; "
            f"expected one of {sorted(_RERANKER_PROVIDERS)}"
        )
    return settings
