"""Application configuration using Pydantic Settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Service
    LOG_LEVEL: str = "INFO"
    INTAKE_API_KEY: str

    # Supplier inbox (Obsidian vault supplier inbox). info.md files discovered
    # here are persisted to Supabase raw source documents and extracted into
    # the universal product schema tables.
    OBSIDIAN_SUPPLIER_PATH: Optional[str] = None

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    # Object Storage (S3-compatible)
    S3_ENDPOINT: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_BUCKET: str

    # Processing limits
    MAX_BATCH_IMAGES: int = 100
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: str = ".md,.pdf,.png,.jpg,.jpeg,.tiff,.xlsx,.xls"

    @property
    def allowed_extensions_set(self) -> set[str]:
        return {ext.strip().lower() for ext in self.ALLOWED_EXTENSIONS.split(",")}


settings = Settings()