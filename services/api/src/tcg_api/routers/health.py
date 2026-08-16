"""The health endpoint.

Deliberately dependency-free — no database, no network, no filesystem — so it is
usable as a container readiness probe and answers even when a downstream
dependency is unavailable. Dependency checks belong to a separate readiness
endpoint.

It also publishes the ``application_version`` that spec §57 requires every
analysis's reproducibility record to carry, which makes a running deployment
self-identifying.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tcg_api.version import application_version

__all__ = ["HealthResponse", "router"]

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """The body of a successful ``GET /health``.

    A typed model rather than a bare dict because the OpenAPI schema is the sole
    source of frontend types — see ADR 0001.
    """

    status: Literal["ok"] = Field(
        description="Liveness of the service itself. Dependencies are not consulted.",
    )
    application_version: str = Field(
        description=(
            "Version of the running application, recorded against every analysis "
            "for reproducibility (spec §57)."
        ),
        examples=["0.0.0"],
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Report service health",
    description=(
        "Returns the liveness of the API and the version of the running "
        "application. Consults no database, network or filesystem, so it is safe "
        "to use as a readiness probe."
    ),
)
async def read_health() -> HealthResponse:
    """Report that the service is up, and which version of it is up."""
    return HealthResponse(status="ok", application_version=application_version())
