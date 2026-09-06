"""Readiness probe.

`/health` is a liveness probe: cheap, dependency-free, and true whenever the
process is running. `/readiness` is the separate question — can this process
actually serve traffic right now? — and it is separate precisely so that a
database outage never makes the liveness probe fail and get the container
killed.

The frozen contract:

    GET /readiness -> 200 | 503
    {
      "status": "ok" | "degraded",
      "checks": {
        "database": "ok" | "unavailable",
        "storage":  "ok" | "unavailable",
        "redis":    "ok" | "unavailable" | "not_configured"
      }
    }

Redis has a third answer because an unset `TCG_API_REDIS_URL` is allowed on
the limiter's terms (ADR 0005): the service starts and does not limit. That
is reported, not failed. A configured Redis that does not answer is a dead
queue — analyses pile up in `uploaded` — and degrades like the other two.
"""

from __future__ import annotations

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from tcg_api.database import check_database_connectivity, get_engine
from tcg_api.rate_limit import get_redis
from tcg_api.storage import check_object_storage_connectivity, get_object_storage

logger = structlog.get_logger(__name__)

RedisCheck = Literal["ok", "unavailable", "not_configured"]

router = APIRouter(tags=["health"])


class ReadinessChecks(BaseModel):
    """Per-dependency outcome. Further dependencies join this model as they land."""

    database: Literal["ok", "unavailable"] = Field(
        description="Whether the API could execute a trivial statement against PostgreSQL.",
    )
    storage: Literal["ok", "unavailable"] = Field(
        description="Whether the API could reach the object store.",
    )
    redis: RedisCheck = Field(
        description=(
            "Whether the API could PING the queue's Redis. `not_configured` when "
            "`TCG_API_REDIS_URL` is unset, which does not degrade the service."
        ),
    )


class ReadinessResponse(BaseModel):
    """apps/web generates its types from this schema — see ADR 0001."""

    status: Literal["ok", "degraded"] = Field(
        description="`degraded` whenever any check failed; the response is then HTTP 503.",
    )
    checks: ReadinessChecks


async def database_is_reachable() -> bool:
    """Dependency wrapping the connectivity probe, so tests can override it.

    Building the engine sits inside the guard because it is itself a thing that
    can fail: `get_engine` reads `TCG_API_DATABASE_URL`, and an unset or
    malformed value raises before any connection is attempted. Letting that
    escape would answer a readiness probe with a 500 — "this probe is broken" —
    when the truthful answer is a 503 naming the database as unavailable.
    Misconfiguration is the most likely reason a fresh deployment is not ready,
    so it is the last thing that should surface as a crash.
    """
    try:
        engine = get_engine()
    except Exception:
        logger.warning("readiness.database_engine_unavailable", exc_info=True)
        return False
    return await check_database_connectivity(engine)


async def object_storage_is_reachable() -> bool:
    """Dependency wrapping the storage probe, so tests can override it.

    Construction sits inside the guard for the same reason it does above:
    `get_object_storage` reads the `TCG_API_STORAGE_*` variables, and an
    unconfigured store raises before any request is attempted. That is a 503
    naming storage, not a 500 saying the probe itself is broken.
    """
    try:
        storage = get_object_storage()
    except Exception:
        logger.warning("readiness.object_storage_unavailable", exc_info=True)
        return False
    return await check_object_storage_connectivity(storage)


async def redis_is_reachable() -> RedisCheck:
    """Dependency wrapping a PING, so tests can override it.

    The limiter's own client, with its own socket timeouts — a hung Redis must
    not hang the probe any more than it may hang a request. (redis-py retries
    a refused or timed-out connection three times with backoff by default, so
    the probe's worst case under an outage is seconds, not the quarter-second
    socket timeout; it still answers `degraded`.) `get_redis` raises
    `RuntimeError` when the URL is unset, which is the one construction
    failure that is not an outage.
    """
    try:
        client = get_redis()
    except RuntimeError:
        return "not_configured"
    except Exception:
        logger.warning("readiness.redis_unavailable", exc_info=True)
        return "unavailable"
    try:
        await client.ping()
    except Exception:
        logger.warning("readiness.redis_unavailable", exc_info=True)
        return "unavailable"
    return "ok"


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    summary="Report whether the API can serve traffic",
    responses={
        status.HTTP_200_OK: {
            "model": ReadinessResponse,
            "description": "Every dependency answered.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "At least one dependency did not answer.",
        },
    },
)
async def readiness(
    response: Response,
    database_reachable: Annotated[bool, Depends(database_is_reachable)],
    storage_reachable: Annotated[bool, Depends(object_storage_is_reachable)],
    redis: Annotated[RedisCheck, Depends(redis_is_reachable)],
) -> ReadinessResponse:
    """Report dependency health.

    Degrades to 503 with a body rather than raising: an orchestrator reading a
    500 from a readiness probe learns only that the probe is broken, whereas a
    503 with `checks` names the dependency that is down.

    Every check is reported, not just the first failure: an operator fixing a
    deployment wants the whole list, and a probe that stops at the first problem
    turns one outage into two round trips.
    """
    checks = ReadinessChecks(
        database="ok" if database_reachable else "unavailable",
        storage="ok" if storage_reachable else "unavailable",
        redis=redis,
    )

    if not (database_reachable and storage_reachable) or redis == "unavailable":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", checks=checks)

    return ReadinessResponse(status="ok", checks=checks)
