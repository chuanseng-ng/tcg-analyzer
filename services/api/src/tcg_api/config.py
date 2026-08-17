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
from urllib.parse import urlparse

from pydantic import AfterValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

__all__ = [
    "DATABASE_URL_ENV_VAR",
    "STORAGE_ACCESS_KEY_ID_ENV_VAR",
    "STORAGE_BUCKET_ENV_VAR",
    "STORAGE_ENDPOINT_URL_ENV_VAR",
    "STORAGE_SECRET_ACCESS_KEY_ENV_VAR",
    "Settings",
    "get_settings",
]

DATABASE_URL_ENV_VAR = "TCG_API_DATABASE_URL"
STORAGE_ACCESS_KEY_ID_ENV_VAR = "TCG_API_STORAGE_ACCESS_KEY_ID"
STORAGE_BUCKET_ENV_VAR = "TCG_API_STORAGE_BUCKET"
STORAGE_ENDPOINT_URL_ENV_VAR = "TCG_API_STORAGE_ENDPOINT_URL"
# bandit reads any string named `..._KEY` as a credential. This one is the name
# of an environment variable, which is the opposite: it exists so that error
# messages can name the thing to set without naming its value.
STORAGE_SECRET_ACCESS_KEY_ENV_VAR = "TCG_API_STORAGE_SECRET_ACCESS_KEY"  # noqa: S105


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


def _require_absolute_url(value: str) -> str:
    """Reject a storage endpoint boto3 could never resolve.

    An endpoint missing its scheme is the overwhelmingly common mistake
    (``localhost:9000`` rather than ``http://localhost:9000``), and botocore's
    own message for it names neither the variable nor the omission.
    """
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"must be an absolute http(s) URL such as http://localhost:9000, got {value!r}"
        )
    return value


StorageEndpointUrl = Annotated[str, AfterValidator(_require_absolute_url)]


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

    # ----------------------------------------------------------------
    # Object storage — the S3-compatible adapter in tcg_shared.storage.s3.
    #
    # These name S3 concepts because an adapter's configuration legitimately
    # does. The port does not: nothing below crosses into `tcg_shared.storage`,
    # which is what keeps the provider replaceable (ADR 0002).
    # ----------------------------------------------------------------

    storage_endpoint_url: StorageEndpointUrl | None = Field(
        default=None,
        validation_alias=STORAGE_ENDPOINT_URL_ENV_VAR,
        description="S3-compatible endpoint, e.g. http://localhost:9000 for MinIO",
    )
    """Where the object store lives. ``None`` means real AWS S3, whose endpoint
    boto3 derives from the region."""

    storage_bucket: str | None = Field(
        default=None,
        validation_alias=STORAGE_BUCKET_ENV_VAR,
        description="Bucket every storage key is relative to",
    )
    """The bucket. ``None`` means unconfigured, not invalid."""

    storage_region: str = "us-east-1"
    """The region to sign for. MinIO ignores it, but a v4 signature needs one."""

    storage_access_key_id: str | None = Field(
        default=None,
        validation_alias=STORAGE_ACCESS_KEY_ID_ENV_VAR,
        description="Access key for the object store",
    )
    """Access key. Never defaulted to a real credential (spec §77)."""

    storage_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias=STORAGE_SECRET_ACCESS_KEY_ENV_VAR,
        description="Secret key for the object store",
    )
    """Secret key, wrapped so it cannot be printed into a log line by accident.

    ``SecretStr`` renders as ``**********`` in reprs and in structlog output;
    reading it requires an explicit ``.get_secret_value()``, which is visible in
    review in a way that a bare string never is.
    """

    storage_signed_url_ttl_seconds: int = Field(default=900, gt=0)
    """How long a signed URL stays valid.

    Short by design: a signed URL is a bearer credential nobody can revoke, so
    the only bound on its misuse is how quickly it stops working (spec §55).
    """


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached because settings are immutable for the process lifetime and are read
    on every request path that needs them. Tests that manipulate the environment
    must call ``get_settings.cache_clear()``.
    """
    return Settings()
