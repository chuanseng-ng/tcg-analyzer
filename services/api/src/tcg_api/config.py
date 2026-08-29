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

import re
from functools import lru_cache
from typing import Annotated, Final, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

__all__ = [
    "ANNOTATOR_ID_PATTERN",
    "DATABASE_URL_ENV_VAR",
    "REDIS_URL_ENV_VAR",
    "STORAGE_ACCESS_KEY_ID_ENV_VAR",
    "STORAGE_BUCKET_ENV_VAR",
    "STORAGE_ENDPOINT_URL_ENV_VAR",
    "STORAGE_SECRET_ACCESS_KEY_ENV_VAR",
    "Settings",
    "get_settings",
]

DATABASE_URL_ENV_VAR = "TCG_API_DATABASE_URL"
REDIS_URL_ENV_VAR = "TCG_API_REDIS_URL"
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


def _require_redis_url(value: str) -> str:
    """Reject a broker URL Celery could never connect to.

    Only the scheme and a host are checked. Whether the *deployment* uses
    `rediss://` and credentials is a matter for the deployment — local
    development runs plain http against MinIO and plain TCP against PostgreSQL,
    and refusing `redis://` here would only mean a second setting to turn the
    check off.
    """
    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
        raise ValueError(
            "must be a redis:// or rediss:// URL such as "
            f"redis://:password@localhost:6379/0, got {value!r}"
        )
    return value


RedisUrl = Annotated[str, AfterValidator(_require_redis_url)]


#: The grammar `image_annotations.annotator_id` and
#: `centering_measurements.annotator_id` are constrained to (#158). Spec §53's
#: restraint made structural: there is no "@" and no space in it, so a name or an
#: email address is not storable. Repeated here rather than imported from
#: `tcg_api.datasets.tables`, which would pull the whole schema into settings for
#: one string; `test_config.py` holds the two together by value.
ANNOTATOR_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _require_opaque_annotator_id(value: str) -> str:
    """Reject an annotator identifier the annotation tables would refuse.

    The CHECK is the guarantee and this is the message — #154's rule. Refused
    here, the operator is told at startup which variable is wrong; refused only
    by PostgreSQL, they find out when an annotator loses an afternoon's work to
    a 500.
    """
    if not ANNOTATOR_ID_PATTERN.match(value):
        raise ValueError(
            "must be an opaque identifier matching "
            f"{ANNOTATOR_ID_PATTERN.pattern} — lower case, no spaces and no '@', "
            "so that a name or an email address is not storable (spec §53). "
            f"Got {value!r}"
        )
    return value


AnnotatorId = Annotated[str, AfterValidator(_require_opaque_annotator_id)]


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

    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    """Browser origins permitted to call this API.

    The defaults are the two Next.js development servers: ``apps/web`` on :3000
    and ``apps/annotation`` on :3001, both of which must reach this service on
    :8000.

    A deployment lists neither. `apps/annotation` reads `/internal/annotation`,
    which ADR 0009 keeps off the public origin — so the deployed tool is served
    from wherever that ingress lives, and this default is a local convenience
    rather than a statement about where the two applications belong.
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
    # Anonymous sessions — spec §53, §54.
    #
    # V1 has no accounts, so a session is the whole of a user's continuity and
    # it is the only thing separating one anonymous user's photographs from
    # another's. Both settings below are policy rather than plumbing, which is
    # why they are configuration a reviewer can see rather than constants.
    # ----------------------------------------------------------------

    session_ttl_seconds: int = Field(default=604_800, gt=0)
    """How long an anonymous session, and everything hanging off it, is kept.

    Seven days. Spec §54 makes expiry the default and retention the exception,
    and `analysis_sessions.expires_at` is deliberately `NOT NULL` with no server
    default so the period lands somewhere it can be reviewed. Long enough that a
    user who photographs a card and comes back tomorrow still has their
    analysis; short enough to be a defensible answer to "why are you still
    holding a picture of my living room".
    """

    session_cookie_secure: bool = False
    """Whether the session cookie is marked `Secure`.

    ``False`` by default because local development is plain http and a `Secure`
    cookie would simply never be sent. Set it wherever https terminates: the
    cookie is a bearer token, and without this a downgrade to http leaks it.
    """

    # ----------------------------------------------------------------
    # The job queue — spec §8.
    #
    # Named for Redis rather than for Celery deliberately. Redis arrives with
    # the job runner, and the rate limiter (#98) needs a shared store next; a
    # `celery_broker_url` would invite a second Redis for the second consumer.
    # ----------------------------------------------------------------

    redis_url: RedisUrl | None = Field(
        default=None,
        validation_alias=REDIS_URL_ENV_VAR,
        description="Redis URL for the job queue, e.g. redis://:password@localhost:6379/0",
    )
    """Where Redis lives. ``None`` means unconfigured, not invalid.

    Absent is allowed on the same terms as an absent ``database_url``: the
    service starts and ``/health`` answers. Only the endpoints that enqueue work
    fail, and they fail as 503s naming this variable rather than as a startup
    crash a deployment without a worker would hit for no reason.

    **Deployment must use ``rediss://`` with credentials.** An unauthenticated
    broker reachable from anywhere lets an attacker enqueue tasks straight into
    the worker, which is the best-known Celery attack path after pickle. The
    local Compose file sets a password for the same reason, even though nothing
    outside the machine can reach it.
    """

    # ----------------------------------------------------------------
    # Rate limiting — spec §55, #98, ADR 0005.
    #
    # Counted in the same Redis, which is the whole reason `redis_url` is named
    # for the store. An absent `redis_url` means no limiting at all, on the same
    # terms an absent `database_url` means an unready service rather than a
    # startup crash.
    # ----------------------------------------------------------------

    rate_limit_requests: int = Field(default=30, gt=0)
    """How many requests one client may make to the limited endpoints per window.

    Thirty a minute is generous for a person — starting an analysis, uploading
    two photographs and running it is four or five requests, and a retake is a
    couple more — and restrictive for a script. Clients behind one address share
    a bucket, which is the cost of keying on an address at all (ADR 0005); raise
    it where an office or a mobile carrier NAT would otherwise be one user.
    """

    rate_limit_window_seconds: int = Field(default=60, gt=0)
    """The window `rate_limit_requests` is counted over.

    A fixed window, so a client can send up to twice the limit across a boundary.
    That is a known and deliberate ceiling; see `tcg_api.rate_limit`.
    """

    # ----------------------------------------------------------------
    # Image uploads — spec §55, §56, #33.
    #
    # Two limits rather than one, because they defend against different things.
    # A byte limit bounds what crosses the network; a pixel limit bounds what a
    # decoder allocates, and a small file can declare an enormous bitmap. Both
    # are policy, so both are configuration a reviewer can see.
    # ----------------------------------------------------------------

    upload_max_bytes: int = Field(default=15 * 1024 * 1024, gt=0)
    """The largest image body the upload endpoint will accept, in bytes.

    Fifteen mebibytes. A twelve-megapixel phone photograph is three to eight,
    and a forty-eight-megapixel one is around twelve. Enforced while the body is
    still being read, so an oversized upload is refused rather than buffered.
    """

    upload_max_pixels: int = Field(default=50_000_000, gt=0)
    """The largest decoded bitmap the upload endpoint will produce, in pixels.

    The decompression-bomb ceiling (spec §55). Checked against the dimensions in
    the file's header *before* anything is decoded, so a two-kilobyte PNG
    declaring 60000 by 60000 costs nothing to refuse. Fifty megapixels is
    comfortably above any camera a user will hold and comfortably below what
    would exhaust a container.
    """

    # ----------------------------------------------------------------
    # Market data — spec §37, §38, #55, #56.
    # ----------------------------------------------------------------

    market_stale_after_days: int = Field(default=30, gt=1)
    """How old a price has to be before it is worth only `STALE_FLOOR`.

    Configuration rather than a constant because it is a judgement about this
    deployment's ingestion cadence, not a fact about prices —
    `tcg_market_data.freshness` says so and names this variable. `GET
    /cards/{id}/market` reads it; nothing stores a confidence derived from it.

    Thirty days, matching `price_confidence`'s own worked example of a
    month-old price on a thinly traded card. Spec §37 targets a daily refresh,
    so a price this old means ingestion has been failing for weeks.

    `gt=1` rather than `gt=0`: `price_confidence` refuses a `stale_after` that
    is not longer than its one-day fresh window, and refusing it here instead
    means that `ValueError` is unreachable from an HTTP request.
    """

    # ----------------------------------------------------------------
    # The annotation tool — spec §30, #160.
    # ----------------------------------------------------------------

    annotator_id: AnnotatorId = "annotator"
    """Who the internal annotation tool records as the author of a label.

    **Spec §30 asks that the annotator be recorded automatically and never
    typed**, so it is stamped by the service and the tool never sends one — which
    also means the `annotator_id` CHECK on both annotation tables can never be
    reached by client input.

    Configuration rather than a constant because it is the one thing about the
    tool a deployment legitimately differs on, and because a value hard-coded in
    a module is a value nobody can see is being recorded. There is one annotator
    today; `apps/annotation/README.md` makes a second party annotating a new ADR,
    not a new environment variable, and this default is what a single-annotator
    deployment leaves alone.
    """

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
