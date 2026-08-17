"""Environment-driven settings for the API service.

Every setting is read from a ``TCG_API_``-prefixed environment variable so a
deployed container is retuned by configuration rather than by a code change.
This is the single settings object for the service: nothing else may read
``os.environ`` directly, because a variable that only one module knows about is
a variable that never reaches ``.env.example``.

No default here is ever a credential. The repository may be open-sourced
(spec §77), so secrets arrive from the environment and the checked-in
``.env.example`` carries placeholders only.

**Fail fast on malformed, not on absent.** A value the service cannot possibly
use — an unparseable database URL, a synchronous driver where the engine is
async — stops startup with a message naming the environment variable, because
the alternative is a confusing failure on the first request. An *absent*
``TCG_API_DATABASE_URL`` is different: the service starts, ``/health`` answers,
and ``/readiness`` reports the database unavailable. See
``tests/test_readiness_wiring.py`` — that distinction is deliberate and tested.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AfterValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

__all__ = ["DATABASE_URL_ENV_VAR", "Settings", "get_settings"]

DATABASE_URL_ENV_VAR = "TCG_API_DATABASE_URL"


def _require_async_database_url(value: str) -> str:
    """Reject a database URL the async engine could never use.

    Raised as a plain `ValueError`; pydantic wraps it into a `ValidationError`
    whose location is the field's alias, so the operator is told the name of the
    variable to fix rather than the name of a Python attribute.
    """
    try:
        url = make_url(value)
    except ArgumentError as exc:
        raise ValueError(f"is not a valid SQLAlchemy URL: {exc}") from exc

    try:
        dialect = url.get_dialect()
    except NoSuchModuleError as exc:
        raise ValueError(f"names a driver SQLAlchemy does not know: {exc}") from exc

    if not dialect.is_async:
        raise ValueError(
            f"names the synchronous driver {url.drivername!r}, but this service "
            f"uses an async engine. Use an async driver, e.g. postgresql+asyncpg."
        )
    return value


AsyncDatabaseUrl = Annotated[str, AfterValidator(_require_async_database_url)]


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="TCG_API_",
        env_file=".env",
        extra="ignore",
        # So `Settings(database_url=...)` works alongside the explicit alias.
        populate_by_name=True,
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

    database_url: AsyncDatabaseUrl | None = Field(
        default=None,
        # Spelling the variable out rather than relying on `env_prefix` costs a
        # little repetition and buys a validation error that names the thing the
        # operator has to set, instead of the internal field name.
        validation_alias=DATABASE_URL_ENV_VAR,
        description="SQLAlchemy URL, e.g. postgresql+asyncpg://tcg:tcg@localhost:5432/tcg",
    )
    """Where PostgreSQL lives. ``None`` means unconfigured, not invalid."""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached because settings are immutable for the process lifetime and are read
    on every request path that needs them. Tests that manipulate the environment
    must call ``get_settings.cache_clear()``.
    """
    return Settings()
