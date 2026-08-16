"""Environment-driven settings for the API service.

Every setting is read from a ``TCG_API_``-prefixed environment variable so a
deployed container is retuned by configuration rather than by a code change.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="TCG_API_",
        env_file=".env",
        extra="ignore",
    )

    log_level: str = "INFO"
    """Root log level, e.g. ``DEBUG``, ``INFO``, ``WARNING``."""

    log_format: Literal["json", "console"] = "json"
    """``json`` for machine-readable deployment logs, ``console`` for humans."""

    cors_origins: list[str] = ["http://localhost:3000"]
    """Browser origins permitted to call this API.

    The default is the Next.js development server: ``apps/web`` runs on :3000
    and must reach this service on :8000.
    """


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached because settings are immutable for the process lifetime and are read
    on every request path that needs them. Tests that manipulate the environment
    must call ``get_settings.cache_clear()``.
    """
    return Settings()
