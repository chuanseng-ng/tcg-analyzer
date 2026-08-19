"""The card catalog's HTTP surface.

Today that is one endpoint: which catalog this deployment is serving. Spec §57
requires every analysis to record `card_database_version`, and a client that
cannot ask for it has to be told out of band, which is how a reproducibility
record quietly stops being reproducible.

**Not on `GET /health`.** That endpoint is contractually cheap and
dependency-free — a container readiness probe that must answer while PostgreSQL
is down — and this one reads the database. Spec §64's endpoint list is
conceptual and names no catalog-version endpoint; adding one is an addition to
it rather than a deviation from it.

The router holds HTTP and nothing else. The SQL lives in
`tcg_api.catalog.versions`, behind the domain's port, exactly as `readiness.py`
delegates its probe to `tcg_api.database`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from tcg_domain.catalog_version import CardDatabaseVersion, CardDatabaseVersionRepository
from tcg_domain.errors import CatalogUnavailable

from tcg_api.catalog.versions import PostgresCardDatabaseVersionRepository
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse

__all__ = [
    "CatalogVersionResponse",
    "RecordCounts",
    "card_database_version_repository",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

#: Both failure cases answer 503 under `provider_error`, distinguished by
#: `details.reason`. Spec §66's taxonomy has eight codes and no `not_found`, and
#: adding a ninth is a specification change rather than a local decision — but
#: neither case is a client error anyway. "The catalog is unreachable" and "no
#: catalog has been registered" are both this deployment being unable to say
#: which catalog it is serving, which is what a 503 means.
_UNREACHABLE = "The card catalog could not be reached."
_UNREGISTERED = "No card catalog version has been registered."


class RecordCounts(BaseModel):
    """How much of the catalog the run that published this version wrote."""

    sets: int = Field(description="Sets the run wrote.", examples=[4])
    cards: int = Field(description="Cards the run wrote.", examples=[22])
    external_ids: int = Field(description="Provider identifiers the run wrote.", examples=[23])


class CatalogVersionResponse(BaseModel):
    """The body of a successful `GET /catalog/version`.

    `apps/web` generates its types from this model — see ADR 0001.

    The record's surrogate key and its publication ordinal are deliberately
    absent: neither is usable by a client, and publishing the ordinal would
    invite one to sort on an internal key. So is `metadata`, which is free-form
    and would generate as an untyped record rather than a contract.
    """

    version: str = Field(
        description=(
            "The explicit, ordered catalog identifier. Never a moving pointer (spec §31)."
        ),
        examples=["pokemon-catalog-seed-v0.0.0"],
    )
    source: str = Field(
        description="A lowercase slug naming where the catalog came from.",
        examples=["manual"],
    )
    source_license: str | None = Field(
        description=("The licence the source data arrived under, where one applies (ADR 0004)."),
        examples=["MIT"],
    )
    source_revision: str | None = Field(
        description="The upstream revision imported, where the source has one.",
        examples=["8f2c1ab"],
    )
    generated_at: datetime = Field(
        description="When the source data was produced or retrieved.",
    )
    record_counts: RecordCounts


async def card_database_version_repository() -> AsyncIterator[CardDatabaseVersionRepository]:
    """Yield the repository for one request. A dependency so tests can override it.

    Building the session factory sits inside the guard for the reason
    `readiness.py` gives about the engine: it reads `TCG_API_DATABASE_URL`, and
    an unset or malformed value should be the same 503 as an unreachable
    database rather than a 500. A deployment without a database configured
    cannot say which catalog it is serving either.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("catalog session factory could not be built", exc_info=True)
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "catalog_unreachable"},
        ) from error

    async with factory() as session:
        yield PostgresCardDatabaseVersionRepository(session)


def _response(record: CardDatabaseVersion) -> CatalogVersionResponse:
    return CatalogVersionResponse(
        version=record.version,
        source=record.source,
        source_license=record.source_license,
        source_revision=record.source_revision,
        generated_at=record.generated_at,
        record_counts=RecordCounts(
            sets=record.set_count,
            cards=record.card_count,
            external_ids=record.external_id_count,
        ),
    )


@router.get(
    "/version",
    response_model=CatalogVersionResponse,
    summary="Report the card catalog version in use",
    description=(
        "Returns the card catalog version this deployment is serving — spec §57's "
        "`card_database_version`, one of the seven fields an analysis records so "
        "it can be re-derived. Reads the database, which is why it is a separate "
        "endpoint from `/health`."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "The catalog could not be reached, or no version has been registered. "
                "`details.reason` says which."
            ),
        },
    },
)
async def read_catalog_version(
    repository: Annotated[CardDatabaseVersionRepository, Depends(card_database_version_repository)],
) -> CatalogVersionResponse:
    """Report the current catalog version, or say why there is none to report."""
    try:
        current = await repository.current()
    except CatalogUnavailable as error:
        logger.warning("catalog version could not be read", exc_info=True)
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "catalog_unreachable"},
        ) from error

    if current is None:
        # The schema is migrated but nothing has been seeded or imported. Not a
        # client error — there is nothing a different request would find.
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREGISTERED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "no_catalog_version_registered"},
        )

    return _response(current)
