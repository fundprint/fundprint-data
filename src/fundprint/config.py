"""Env-driven configuration loaded once at import time."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config drawn from environment variables; see .env.example."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/fundprint",
        description="psycopg-compatible connection string",
    )
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key for LLM extraction in the Resolve layer",
    )
    snapshot_store_path: str = Field(
        default="./snapshots",
        description="Local path or object-storage prefix for raw snapshot blobs",
    )
    # The model name is stored alongside every embedding row so that
    # cross-model cosine comparisons can be blocked at query time.
    embedding_model: str = Field(
        default="voyage-large-2-instruct",
        description="Embedding model identifier stamped on every name_embedding row",
    )


settings = Settings()
