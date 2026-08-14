"""Runtime configuration for a self-owned CLIO Web Search installation."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings loaded from ``CLIO_WEB_SEARCH_`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="CLIO_WEB_SEARCH_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = Path("/var/lib/clio-web-search")
    searxng_url: str = "http://127.0.0.1:8888"
    grobid_url: str = "http://127.0.0.1:8070"
    contact_email: str | None = None
    openalex_api_key: str | None = None
    max_input_bytes: int = 50 * 1024 * 1024
    workers: int = Field(default=1, ge=1, le=16)
    max_pending_jobs: int = Field(default=32, ge=1, le=4096)
    cache_ttl_days: int = Field(default=7, ge=1, le=365)
    cache_max_bytes: int = 10 * 1024 * 1024 * 1024
    conversion_timeout_s: float = 900.0
    request_timeout_s: float = 30.0

    @property
    def database_path(self) -> Path:
        """Return the persistent SQLite path."""

        return self.data_dir / "jobs.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        """Return the persistent input-object directory."""

        return self.data_dir / "objects"

    @property
    def results_dir(self) -> Path:
        """Return the persistent conversion-result directory."""

        return self.data_dir / "results"
