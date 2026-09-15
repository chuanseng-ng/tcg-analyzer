"""Writing normalized quotes to `market_observations`, and the rest to quarantine.

Spec §37 draws ingestion as `raw response → normalization → validation →
Postgres`. `tcg_market_data.normalize` is the middle two, and is pure; this is
the part that needs a database. It resolves each quote's provider identifier to
catalog cards, chooses the rate for its currency and day, and writes either one
observation or one quarantine row. **Every quote lands somewhere**: a silent
drop makes a coverage gap indistinguishable from a bug (issue #53).

**Nothing here commits.** #54's daily worker owns the transaction and cuts
`generate_snapshot` inside it, because the snapshot's `<=` against a shared
`now()` is what includes the run's own rows. `tcg_api.market.snapshots` says
why at length.

**Plain functions and no Protocol**, for the reason `snapshots.py` gives: this
is the repository's own record of its own ingestion, not a replaceable provider.
No provider logic appears here either — a `ProviderQuote` is already neutral, and
#52's adapter is where a vendor's response becomes one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.card import CardReference
from tcg_market_data import (
    MAX_CLOCK_SKEW,
    MAX_OBSERVATION_AGE,
    MAX_RATE_AGE,
    ExchangeRate,
    NormalizedObservation,
    ProviderQuote,
    QuarantineReason,
    normalize,
)

from tcg_api.catalog.cards import CARD_SELECT, card_entity
from tcg_api.catalog.tables import card_external_ids, cards
from tcg_api.database import execute
from tcg_api.market.tables import exchange_rates, market_observations, market_quarantine

__all__ = ["MarketNormalizationUnavailable", "NormalizationReport", "record_quotes"]

logger = structlog.get_logger(__name__)

_UNAVAILABLE: Final = "The market data store could not be reached."

#: Rows per INSERT. asyncpg caps one statement at 32,767 bind parameters, and an
#: observation row carries twelve: a full 49,399-card run in one VALUES list
#: would be refused outright. 1,000 rows is 12,000 parameters.
#:
#: ponytail: a multi-row VALUES per chunk, not COPY. COPY is the upgrade if #54
#: measures the write as the slow part of a run; it is ~50 statements today.
_CHUNK: Final = 1_000


class MarketNormalizationUnavailable(ConnectionError):
    """The market store could not be read or written while recording quotes.

    **Not spec §66's `market_data_unavailable`**, and not
    `MarketSnapshotUnavailable`: this is the ingestion side failing to reach the
    store, and #54 retries a run on it.
    """


@dataclass(frozen=True, slots=True)
class NormalizationReport:
    """What one call stored, what it quarantined and why, and what did not map.

    Args:
        stored: Observations written.
        quarantined: Quarantine rows written, by reason. Absent reasons are
            absent, not zero.
        unmapped_external_ids: Provider identifiers no catalog card carries, in
            the order first seen. A catalog problem worth reporting — #54 turns
            it into a coverage alert — never something to invent a card for.
    """

    stored: int
    quarantined: Mapping[QuarantineReason, int]
    unmapped_external_ids: tuple[str, ...]


async def record_quotes(
    db: AsyncSession,
    *,
    provider_id: UUID,
    provider: str,
    quotes: Sequence[ProviderQuote],
    now: datetime,
) -> NormalizationReport:
    """Normalize `quotes` and write each one as an observation or a quarantine row.

    Does not commit; the caller owns the transaction.

    Args:
        db: The session whose transaction the rows join.
        provider_id: The `market_providers` row the quotes came from.
        provider: That row's slug — what `card_external_ids.provider` spells the
            source with, and what each observation is stamped with.
        quotes: What the provider said.
        now: The moment of normalizing, timezone-aware. A parameter rather than
            the clock so a run judges every quote against one instant.

    Raises:
        MarketNormalizationUnavailable: If the store could not be reached.
    """
    if not quotes:
        return NormalizationReport(stored=0, quarantined={}, unmapped_external_ids=())

    cards_by_id = await _cards(db, provider, quotes)
    rates_by_currency = await _rates(db, quotes, now)

    observations: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    unmapped: dict[str, None] = {}
    for quote in quotes:
        candidates = cards_by_id.get(quote.external_id, ())
        result = normalize(
            quote,
            provider=provider,
            cards=tuple(reference for _, reference in candidates),
            rate=_rate_for(quote, rates_by_currency),
            now=now,
        )
        if isinstance(result, NormalizedObservation):
            observations.append(_observation_row(result, quote, candidates[0][0], provider_id))
            continue
        if result.reason is QuarantineReason.UNMAPPED_CARD:
            unmapped[str(quote.external_id)] = None
        quarantined.append(
            {
                "id": uuid4(),
                "provider_id": provider_id,
                "external_id": None if quote.external_id is None else str(quote.external_id),
                "reason": result.reason.value,
                "detail": result.detail,
                "record": quote.as_record(),
            }
        )

    await _insert(db, market_observations, observations)
    await _insert(db, market_quarantine, quarantined)

    report = NormalizationReport(
        stored=len(observations),
        quarantined=dict(Counter(QuarantineReason(row["reason"]) for row in quarantined)),
        unmapped_external_ids=tuple(unmapped),
    )
    # Counts only. A quote's content is the provider's data, and ADR 0006's
    # redistribution test is not something a log line should be tested against.
    logger.info(
        "market.quotes_normalized",
        provider=provider,
        stored=report.stored,
        quarantined={reason.value: count for reason, count in report.quarantined.items()},
        unmapped=len(report.unmapped_external_ids),
    )
    return report


async def _cards(
    db: AsyncSession, provider: str, quotes: Iterable[ProviderQuote]
) -> dict[str, tuple[tuple[UUID, CardReference], ...]]:
    """Every catalog card each quote's identifier names, for this provider only.

    One query for the whole call. More than one card per identifier is kept,
    not collapsed: `ix_card_external_ids_provider_external_id` is deliberately
    non-unique, and `normalize` quarantines the ambiguity rather than guessing.
    """
    identifiers = sorted(
        {quote.external_id for quote in quotes if isinstance(quote.external_id, str)}
    )
    if not identifiers:
        return {}
    statement = (
        CARD_SELECT.add_columns(card_external_ids.c.external_id)
        .join(card_external_ids, card_external_ids.c.card_id == cards.c.id)
        .where(
            card_external_ids.c.provider == provider,
            card_external_ids.c.external_id.in_(identifiers),
        )
        .order_by(card_external_ids.c.external_id, cards.c.id)
    )
    result = await execute(
        db, statement, unavailable=MarketNormalizationUnavailable, message=_UNAVAILABLE
    )
    found: dict[str, list[tuple[UUID, CardReference]]] = {}
    for row in result:
        card = card_entity(row)
        found.setdefault(row.external_id, []).append((UUID(str(card.id)), card.reference))
    return {identifier: tuple(members) for identifier, members in found.items()}


async def _rates(
    db: AsyncSession, quotes: Iterable[ProviderQuote], now: datetime
) -> dict[str, tuple[ExchangeRate, ...]]:
    """Every rate a plausible quote could use, newest first per currency.

    Bounded by the window `normalize` accepts an observation in, widened by how
    old a rate may be — a quote outside it is quarantined before its rate is
    ever read, so a rate outside it could never be used.
    """
    currencies = sorted(
        {quote.currency for quote in quotes if isinstance(quote.currency, str)} - {"SGD"}
    )
    if not currencies:
        return {}
    earliest = (now - MAX_OBSERVATION_AGE - MAX_RATE_AGE).date()
    latest = (now + MAX_CLOCK_SKEW).date()
    statement = (
        sa.select(
            exchange_rates.c.id,
            exchange_rates.c.base_currency,
            exchange_rates.c.rate,
            exchange_rates.c.as_of,
        )
        .where(
            exchange_rates.c.base_currency.in_(currencies),
            exchange_rates.c.quote_currency == "SGD",
            exchange_rates.c.as_of.between(earliest, latest),
        )
        .order_by(exchange_rates.c.base_currency, exchange_rates.c.as_of.desc())
    )
    result = await execute(
        db, statement, unavailable=MarketNormalizationUnavailable, message=_UNAVAILABLE
    )
    found: dict[str, list[ExchangeRate]] = {}
    for row in result:
        found.setdefault(row.base_currency, []).append(
            ExchangeRate(id=row.id, base_currency=row.base_currency, rate=row.rate, as_of=row.as_of)
        )
    return {currency: tuple(rates) for currency, rates in found.items()}


def _rate_for(
    quote: ProviderQuote, rates: Mapping[str, tuple[ExchangeRate, ...]]
) -> ExchangeRate | None:
    """The newest rate dated on or before the day the quote was observed.

    A rate dated after the observation is never chosen, even when it is the
    only one: the price was seen before that rate existed. Whether the rate is
    recent enough is `normalize`'s call, not this one's.
    """
    if not isinstance(quote.currency, str) or not isinstance(quote.observed_at, datetime):
        return None
    observed_on = quote.observed_at.date()
    return next((rate for rate in rates.get(quote.currency, ()) if rate.as_of <= observed_on), None)


def _observation_row(
    result: NormalizedObservation, quote: ProviderQuote, card_id: UUID, provider_id: UUID
) -> dict[str, Any]:
    observation = result.observation
    return {
        "id": uuid4(),
        "card_id": card_id,
        "provider_id": provider_id,
        "grading_company": observation.grading_company,
        "grade": None if observation.grade is None else str(observation.grade),
        "currency": result.source_currency,
        "price": result.source_price,
        "price_sgd": observation.price.amount,
        "exchange_rate_id": None if result.exchange_rate is None else result.exchange_rate.id,
        "confidence": observation.confidence.value,
        "observed_at": observation.observed_at,
        # Through `as_record`, so a NaN or a Decimal the provider nested in its
        # metadata cannot fail the INSERT for every good quote beside it.
        "metadata": quote.as_record()["metadata"],
    }


async def _insert(db: AsyncSession, table: sa.Table, rows: Sequence[dict[str, Any]]) -> None:
    for chunk in batched(rows, _CHUNK):
        await execute(
            db,
            sa.insert(table).values(list(chunk)),
            unavailable=MarketNormalizationUnavailable,
            message=_UNAVAILABLE,
        )
