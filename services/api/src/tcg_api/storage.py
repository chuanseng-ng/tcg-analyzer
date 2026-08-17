"""Object-storage wiring for the API service.

The port and its adapters live in `packages/shared`; this module is only the
binding — it turns environment configuration into one concrete
:class:`~tcg_shared.storage.port.ObjectStorage` and hands it to whatever asks.
Deliberately thin: everything a future service (analysis, ingestion) would need
is in the package, and only the choice of adapter is here.

Structured exactly like `database.py` — `create_*`, a cached `get_*`, and a
connectivity probe that returns `False` rather than raising — because the two
answer the same operational questions and should not have to be learned twice.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from tcg_shared.storage import ObjectNotFound, ObjectStorage, StorageKey
from tcg_shared.storage.s3 import S3ObjectStorage, create_s3_client

from tcg_api.config import (
    STORAGE_ACCESS_KEY_ID_ENV_VAR,
    STORAGE_BUCKET_ENV_VAR,
    STORAGE_ENDPOINT_URL_ENV_VAR,
    STORAGE_SECRET_ACCESS_KEY_ENV_VAR,
    Settings,
    get_settings,
)

logger = logging.getLogger(__name__)

__all__ = [
    "check_object_storage_connectivity",
    "create_object_storage",
    "get_object_storage",
]

#: A key that cannot exist, used only to ask the store whether it is answering.
#: Reading a missing object is the cheapest request that still proves
#: credentials, bucket and network all work — `ObjectNotFound` is a *success*
#: for this purpose, which is why the probe below treats it as one.
_PROBE_KEY = StorageKey("readiness/probe")


def create_object_storage(settings: Settings | None = None) -> ObjectStorage:
    """Build the object store described by `settings`.

    Unconfigured storage raises here rather than at `Settings` construction, for
    the same reason an unconfigured database does: a service with no bucket must
    still start and report itself unready (`routers/readiness.py`). The message
    names the variable so the readiness log says what to set.

    Constructing the client performs no network I/O, so this succeeds while the
    store is down — which is what lets the probe distinguish "misconfigured"
    from "unreachable".
    """
    settings = settings or get_settings()
    bucket = settings.storage_bucket
    access_key_id = settings.storage_access_key_id
    secret_access_key = settings.storage_secret_access_key

    # Spelled out as one condition rather than derived from the list below, so
    # that the narrowing is visible to a reader and to mypy alike.
    if bucket is None or access_key_id is None or secret_access_key is None:
        missing = [
            name
            for name, value in (
                (STORAGE_BUCKET_ENV_VAR, bucket),
                (STORAGE_ACCESS_KEY_ID_ENV_VAR, access_key_id),
                (STORAGE_SECRET_ACCESS_KEY_ENV_VAR, secret_access_key),
            )
            if value is None
        ]
        raise RuntimeError(
            f"object storage is not configured: {', '.join(missing)} "
            f"{'are' if len(missing) > 1 else 'is'} not set. For local development set "
            f"{STORAGE_ENDPOINT_URL_ENV_VAR}=http://localhost:9000 and start MinIO with "
            f"docker compose -f infrastructure/local/docker-compose.yml up -d --wait. "
            f"See .env.example."
        )

    return S3ObjectStorage(
        client=create_s3_client(
            endpoint_url=settings.storage_endpoint_url,
            region=settings.storage_region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key.get_secret_value(),
        ),
        bucket=bucket,
    )


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorage:
    """Return the process-wide object store, created on first use.

    Lazy, so importing this module never requires configuration; only actually
    reaching for storage does.
    """
    return create_object_storage()


async def check_object_storage_connectivity(storage: ObjectStorage) -> bool:
    """Return whether `storage` is currently answering.

    Returns `False` rather than raising, because the only caller is a readiness
    probe and a probe that propagates reports a 500 when it means "not ready".

    A miss counts as reachable: `ObjectNotFound` is the store answering a
    well-formed question. Anything else — bad credentials, absent bucket, no
    route — arrives as `StorageUnavailable` and is the failure being looked for.
    """
    try:
        await storage.get(_PROBE_KEY)
    except ObjectNotFound:
        return True
    except Exception:
        logger.warning("object storage connectivity check failed", exc_info=True)
        return False
    # A real object under the probe key would be surprising, but it is still the
    # store answering.
    return True
