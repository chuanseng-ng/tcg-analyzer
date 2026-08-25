"""`GET /cards/{card_id}/market` — spec §64's market endpoint, §6's price panel.

**Served entirely from a snapshot, never from a provider.** Spec §37 is blunt
about it — "do not call an external marketplace for every user analysis" — and
nothing in this module can reach one: it reads `market_observations` through
`tcg_api.market.snapshots`, which was ingested out of band. That is what a
snapshot is for.

**Every price carries its own confidence and age.** A card can have a raw price
from this morning and a PSA 10 price from six weeks ago, and a single
response-level confidence would hide exactly the gap that matters. Both are
computed at the moment of asking — `tcg_market_data.freshness` explains why age
is a question rather than a column — so this response is never cached.

**A missing price is present and null, never omitted.** The ladder is every
grade every supported company can issue, read from the same `ADAPTERS` that
`GET /grading-companies` serves, so a gap in the data is visibly a gap rather
than something a client has to tell apart from a bug. It is also what keeps ADR
0006's "TAG is `insufficient_information` in V1" structural: TAG's grades come
back null because no observation exists, not because a branch here says so, and
there is nowhere for a substituted PSA price to be introduced.

**No price history, deliberately.** #56's issue lists it as optional scope, and
ADR 0006 forbids it: the redistribution test there is functional, so a public
price-history endpoint is out now and later. `?snapshot_id=` covers the
legitimate need behind it — reproducing what a past analysis saw — and
**nothing lists snapshots**, so the parameter takes an unguessable identifier a
caller can only have got from its own analysis's §57 record. Adding a
snapshot-listing route would turn this endpoint into the forbidden one by
iteration.

**No attribution.** ADR 0006 read the terms end to end: none is owed. There is
therefore nothing here that names the vendor, and no join to
`market_providers` to find out.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, Field
from tcg_domain.catalog import Card, CardId
from tcg_domain.errors import CatalogUnavailable
from tcg_domain.repository import CardRepository
from tcg_grading_companies.companies import ADAPTERS
from tcg_market_data import MarketSnapshot, PriceObservation, price_age, price_confidence

from tcg_api.catalog.cards import PostgresCardRepository
from tcg_api.config import Settings, get_settings
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.market.snapshots import (
    MarketSnapshotUnavailable,
    current_snapshot,
    get_snapshot,
    resolve_prices,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CardMarketResponse",
    "GradedPriceResponse",
    "MarketSnapshotResponse",
    "PriceResponse",
    "ResolvedMarket",
    "resolved_market",
    "router",
]

# The `/cards` prefix is shared with `routers/cards.py`, which owns the catalog.
# Two routers, one prefix: `/cards/{card_id}` and `/cards/{card_id}/market` are
# different paths, so neither shadows the other whatever order they mount in.
router = APIRouter(prefix="/cards", tags=["market"])

_UNKNOWN_CARD: Final = "No card is recorded under that identifier."
_NO_SNAPSHOT: Final = "No market data has been ingested yet."
_UNKNOWN_SNAPSHOT: Final = "No market snapshot was generated under that identifier."
_CATALOG_UNREACHABLE: Final = "The card catalog could not be reached."
_MARKET_UNREACHABLE: Final = "The market data store could not be reached."

#: `no-store` rather than no header at all. Without an explicit freshness
#: directive a 200 is heuristically cacheable, and a cached body reports a
#: `price_age` frozen at the moment it was computed — which is precisely spec
#: §38's "do not silently substitute stale data without identifying it". The
#: deliberate opposite of `/grading-companies`, whose body is the same all hour.
_CACHE_CONTROL: Final = "no-store"


class PriceResponse(BaseModel):
    """One price, with what it is currently worth believing."""

    amount: str = Field(
        description=(
            "The amount, as an exact decimal string with two places. A string rather "
            "than a number: a JSON number is a float in most clients, and a rounding "
            "error in a figure a user is deciding money on is not acceptable. "
            "**Zero is a real price** — a card nobody will pay for — and is why an "
            "absent price is `null` rather than `0.00`."
        ),
        examples=["12.30"],
    )
    currency: str = Field(
        description="ISO 4217 code for `amount`.",
        examples=["SGD"],
    )
    price_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Spec §38's `price_confidence`: how much the provider was sure of this "
            "figure, discounted for how long ago it was true. Flat at the provider's "
            "own number for a day, then falling to a floor above zero — old evidence "
            "is still evidence, and reporting it at zero would be indistinguishable "
            "from having none. The provider's undiscounted figure is deliberately "
            "not exposed: only this one is fit to show a user."
        ),
        examples=[0.86],
    )
    price_age_seconds: int = Field(
        ge=0,
        description=(
            "Spec §38's `price_age`: how long before this request the price was "
            "observed. Computed now rather than stored, which is why the response is "
            "not cached. A provider clock running ahead of ours reads as 0, not as a "
            "negative age."
        ),
        examples=[7200],
    )
    observed_at: datetime = Field(
        description="When the provider saw this price. The instant `price_age_seconds` counts from.",
    )


class MarketSnapshotResponse(BaseModel):
    """Which cut of the market these prices came from — spec §36."""

    id: UUID = Field(
        description=(
            "Spec §57's `market_snapshot_id`. Pass it back as `?snapshot_id=` to read "
            "the same prices again however many ingestion runs have happened since."
        ),
    )
    generated_at: datetime = Field(
        description="When the cut was taken. Every price here was stored at or before it.",
    )
    data_version: date = Field(
        description=(
            "The snapshot's date, generated from `generated_at`. **Show this beside "
            "the prices**: a dated record of a past market is honest, where the same "
            "figures presented as current are not."
        ),
        examples=["2026-08-25"],
    )


class GradedPriceResponse(BaseModel):
    """What a card graded by one company at one grade is worth."""

    company: str = Field(
        description="The company's lowercase slug, as `GET /grading-companies` spells it.",
        examples=["psa"],
    )
    grade: str = Field(
        description=(
            "A grade on that company's scale, spelled exactly as "
            "`GET /grading-companies` spells it. The two agree by construction."
        ),
        examples=["10"],
    )
    price: PriceResponse | None = Field(
        description=(
            "`null` when this snapshot holds no price for this company and grade — "
            "which is a fact about the data, never a substituted or interpolated "
            "figure from another company. Present-and-null rather than omitted, so a "
            "client never has to tell a gap apart from a missing field."
        ),
    )


class CardMarketResponse(BaseModel):
    """The body of a successful `GET /cards/{card_id}/market`."""

    card_id: UUID = Field(description="The card these prices are for.")
    snapshot: MarketSnapshotResponse = Field(
        description="The snapshot they were read from. Never null — see the 404 and 503.",
    )
    raw: PriceResponse | None = Field(
        description="The ungraded market price, or `null` when the snapshot holds none.",
    )
    graded: list[GradedPriceResponse] = Field(
        description=(
            "Every grade every supported company can issue, in the order "
            "`GET /grading-companies` lists them and ascending within each. The full "
            "ladder every time, holes included — spec §6's price panel is read down "
            "it, and spec §39's expected value is summed over it."
        ),
    )


@dataclass(frozen=True, slots=True)
class ResolvedMarket:
    """What one request read from the database, before any policy is applied.

    The dependency below does the I/O and this carries the result; the route
    decides what each absence means. That split is what lets a test override one
    dependency and reach every branch with no database at all — the seam
    `routers/grading.py` documents, for the same reason: `tcg_api.market.snapshots`
    is deliberately plain module functions, so the dependency has to be it.

    Both requested identifiers are echoed back rather than re-read from the
    request. `requested_snapshot_id` is load-bearing: without it `snapshot is
    None` cannot tell "this deployment has never ingested" from "the snapshot
    you named was never generated" — one is a 503 and the other a 404.
    """

    requested_card_id: UUID
    requested_snapshot_id: UUID | None
    card: Card | None
    snapshot: MarketSnapshot | None = None
    prices: tuple[PriceObservation, ...] = ()


def _unreachable(reason: str, message: str) -> ApiError:
    """503 `provider_error`, naming which read failed.

    Both stores are the same PostgreSQL, and the reasons stay distinct anyway:
    an operator reading a log learns whether the catalog or the market data was
    the one that would not answer. `market_store_unreachable` rather than
    `market_data_unreachable`, because this route also raises spec §66's
    `market_data_unavailable` and the two must not be misread for each other —
    the first means the question could not be asked, the second that there is no
    price.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        message,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": reason},
    )


async def resolved_market(
    card_id: Annotated[
        UUID,
        Path(description="This catalog's identifier for the card, as returned by a search."),
    ],
    snapshot_id: Annotated[
        UUID | None,
        Query(
            description=(
                "Read a specific snapshot instead of the current one — spec §57's "
                "reproducibility, so a past analysis can be re-read exactly. Omit it "
                "for today's prices."
            ),
        ),
    ] = None,
) -> AsyncIterator[ResolvedMarket]:
    """Read the card, the snapshot and its prices, or answer 503.

    One session for all three: `resolve_prices` needs the `Card` entity, not its
    id, so the catalog read and the market read belong to one transaction and
    the prices cannot come from a snapshot cut for a card that has since
    changed underneath them.

    Short-circuits on an unknown card. The card is the path's resource, so
    `GET /cards/{unknown}/market` is a 404 rather than a 503 even on a
    deployment that has never ingested — and there is no point resolving a
    snapshot for a card that does not exist.

    Building the session factory sits inside the guard for the reason
    `routers/cards.py` gives: it reads `TCG_API_DATABASE_URL`, and an unset or
    malformed value should be the same 503 as an unreachable database rather
    than a 500.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("market session factory could not be built", exc_info=True)
        raise _unreachable("market_store_unreachable", _MARKET_UNREACHABLE) from error

    async with factory() as session:
        repository: CardRepository = PostgresCardRepository(session)
        try:
            card = await repository.get(CardId(card_id))
        except CatalogUnavailable as error:
            logger.warning("card could not be read for a market lookup", exc_info=True)
            raise _unreachable("catalog_unreachable", _CATALOG_UNREACHABLE) from error

        if card is None:
            yield ResolvedMarket(
                requested_card_id=card_id,
                requested_snapshot_id=snapshot_id,
                card=None,
            )
            return

        try:
            snapshot = (
                await current_snapshot(session)
                if snapshot_id is None
                else await get_snapshot(session, snapshot_id)
            )
            prices = () if snapshot is None else await resolve_prices(session, snapshot, card)
        except MarketSnapshotUnavailable as error:
            logger.warning("market prices could not be read", exc_info=True)
            raise _unreachable("market_store_unreachable", _MARKET_UNREACHABLE) from error

        yield ResolvedMarket(
            requested_card_id=card_id,
            requested_snapshot_id=snapshot_id,
            card=card,
            snapshot=snapshot,
            prices=prices,
        )


def _price(observation: PriceObservation, *, at: datetime, stale_after: timedelta) -> PriceResponse:
    return PriceResponse(
        amount=str(observation.price.amount),
        currency=observation.price.currency.value,
        price_confidence=price_confidence(observation, at=at, stale_after=stale_after).value,
        # Seconds rather than a `timedelta`, which pydantic renders as an
        # ISO-8601 duration nobody wants to parse to draw an "updated 2h ago".
        price_age_seconds=int(price_age(observation, at=at).total_seconds()),
        observed_at=observation.observed_at,
    )


def _response(
    card: Card,
    snapshot: MarketSnapshot,
    prices: tuple[PriceObservation, ...],
    *,
    at: datetime,
    stale_after: timedelta,
) -> CardMarketResponse:
    """Lay the snapshot's prices out over the full ladder.

    Keyed on `(grading_company, grade)` because `resolve_prices` returns exactly
    one observation per key. Nothing can be dropped on the way through:
    `PriceObservation.__post_init__` runs `validated_grade_key`, so a stored key
    off a company's scale never becomes an observation in the first place, and
    the ladder below is therefore total over what the query can return.
    """
    held = {
        (observation.grading_company, str(observation.grade)): observation
        for observation in prices
        if observation.grading_company is not None
    }
    raw = next((one for one in prices if one.grading_company is None), None)

    return CardMarketResponse(
        card_id=card.id,
        snapshot=MarketSnapshotResponse(
            id=snapshot.id,
            generated_at=snapshot.generated_at,
            data_version=snapshot.data_version,
        ),
        raw=None if raw is None else _price(raw, at=at, stale_after=stale_after),
        graded=[
            GradedPriceResponse(
                company=company,
                grade=str(grade),
                price=(
                    None
                    if (observation := held.get((company, str(grade)))) is None
                    else _price(observation, at=at, stale_after=stale_after)
                ),
            )
            for company, adapter in ADAPTERS.items()
            for grade in adapter.get_grade_scale().ordered
        ],
    )


@router.get(
    "/{card_id}/market",
    response_model=CardMarketResponse,
    summary="Read one card's market prices from a snapshot",
    description=(
        "Spec §64's market endpoint. Returns the ungraded price and every grade every "
        "supported company can issue, each with spec §38's `price_confidence` and "
        "`price_age` — **per price**, because a fresh raw price and a six-week-old "
        "PSA 10 price on the same card is the gap that matters. Served entirely from "
        "a market snapshot: no provider is called during a request (spec §37), and "
        "the snapshot's `data_version` is returned so a result can be shown for the "
        "date it describes rather than as today's. A price this snapshot does not "
        "hold is `null` rather than absent, and is never filled in from another "
        "company or interpolated. Pass `?snapshot_id=` to re-read exactly what a past "
        "analysis saw. No price history and no fees: the first is out under ADR 0006, "
        "the second is spec §45's user-configured economic input."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": (
                "Either no card is recorded under that identifier — `card_not_identified`, "
                "with `details.card_id` — or no snapshot was generated under the one "
                "`?snapshot_id=` named, which is `market_data_unavailable` with "
                "`details.snapshot_id`."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "`market_data_unavailable` when nothing has been ingested yet, so there "
                "is no snapshot to read. `provider_error` when a store could not be "
                "reached, with `details.reason` of `market_store_unreachable` or "
                "`catalog_unreachable`."
            ),
        },
    },
)
async def read_card_market(
    response: Response,
    resolved: Annotated[ResolvedMarket, Depends(resolved_market)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CardMarketResponse:
    """Return one card's prices as of a market snapshot."""
    if resolved.card is None:
        raise ApiError(
            ErrorCode.CARD_NOT_IDENTIFIED,
            _UNKNOWN_CARD,
            status_code=status.HTTP_404_NOT_FOUND,
            details={"card_id": str(resolved.requested_card_id)},
        )

    snapshot = resolved.snapshot
    if snapshot is None and resolved.requested_snapshot_id is not None:
        # 404, not 422: a malformed UUID here is already FastAPI's 422, and one
        # status meaning two things is what `routers/cards.py` argues against.
        # The same code at two statuses is fine — the endpoint and the status
        # tell them apart, which is the reading `card_not_identified` already
        # gets at 404 and 422.
        raise ApiError(
            ErrorCode.MARKET_DATA_UNAVAILABLE,
            _UNKNOWN_SNAPSHOT,
            status_code=status.HTTP_404_NOT_FOUND,
            details={"snapshot_id": str(resolved.requested_snapshot_id)},
        )
    if snapshot is None:
        # Today's answer everywhere: ADR 0006 gates ingestion on a subscription
        # that is not yet active, so no `market_providers` row exists and
        # nothing has been snapshotted. 503 rather than a 200 with a null
        # snapshot, because the contract is that these prices came from a
        # dated cut — a body that cannot say which one is not this endpoint's
        # answer, it is the absence of one. `GET /catalog/version` refuses the
        # same way when no version is registered.
        raise ApiError(ErrorCode.MARKET_DATA_UNAVAILABLE, _NO_SNAPSHOT)

    response.headers["Cache-Control"] = _CACHE_CONTROL
    # One `at` for the whole response, so fifty-six ages are mutually consistent
    # and two prices observed at the same instant cannot report different ones.
    return _response(
        resolved.card,
        snapshot,
        resolved.prices,
        at=datetime.now(UTC),
        stale_after=timedelta(days=settings.market_stale_after_days),
    )
