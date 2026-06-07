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
    BAILIAN_MODEL: str = "qwen-flash"

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

    # Entity Extraction
    ENABLE_LLM_EXTRACTION: bool = True
    USE_RULE_EXTRACTION: bool = False  # 纯 LLM 模式，不使用规则提取（更快）
    ENTITY_BATCH_SIZE: int = 200  # 实体提取批次大小
    ENTITY_EXTRACTION_DELAY: float = 0

    # CORS - comma-separated list of allowed origins (no wildcards with credentials)
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    # App environment: "development" (default) or "production"
    APP_ENV: str = "development"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


_DEFAULT_JWT_SECRET = "your-secret-key-change-this-in-production"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance. Refuses to start with default JWT_SECRET in production."""
    settings = Settings()
    if settings.JWT_SECRET == _DEFAULT_JWT_SECRET:
        import os
        env = os.environ.get("APP_ENV", "development").lower()
        if env in ("production", "prod"):
            raise RuntimeError(
                "JWT_SECRET is set to the default placeholder in production. "
                "Set a strong random value via the JWT_SECRET environment variable."
            )
    return settings
