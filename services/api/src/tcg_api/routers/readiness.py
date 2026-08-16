"""Readiness probe.

`/health` is a liveness probe: cheap, dependency-free, and true whenever the
process is running. `/readiness` is the separate question — can this process
actually serve traffic right now? — and it is separate precisely so that a
database outage never makes the liveness probe fail and get the container
killed.

The frozen contract:

    GET /readiness -> 200 | 503
    {"status": "ok" | "degraded", "checks": {"database": "ok" | "unavailable"}}
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from tcg_api.database import check_database_connectivity, get_engine

router = APIRouter(tags=["health"])


class ReadinessChecks(BaseModel):
    """Per-dependency outcome. Further dependencies join this model as they land."""

    database: Literal["ok", "unavailable"] = Field(
        description="Whether the API could execute a trivial statement against PostgreSQL.",
    )


class ReadinessResponse(BaseModel):
    """apps/web generates its types from this schema — see ADR 0001."""

    status: Literal["ok", "degraded"] = Field(
        description="`degraded` whenever any check failed; the response is then HTTP 503.",
    )
    checks: ReadinessChecks


async def database_is_reachable() -> bool:
    """Dependency wrapping the connectivity probe, so tests can override it."""
    return await check_database_connectivity(get_engine())


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
    database_reachable: bool = Depends(database_is_reachable),
) -> ReadinessResponse:
    """Report dependency health.

    Degrades to 503 with a body rather than raising: an orchestrator reading a
    500 from a readiness probe learns only that the probe is broken, whereas a
    503 with `checks` names the dependency that is down.
    """
    if not database_reachable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="degraded",
            checks=ReadinessChecks(database="unavailable"),
        )

    return ReadinessResponse(status="ok", checks=ReadinessChecks(database="ok"))
