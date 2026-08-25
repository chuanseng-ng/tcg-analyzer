"""Generating and resolving spec §36's market snapshots.

`tables.py` says what a `market_snapshots` row *is*; this module is the only
place one is written or read. The split matches the catalog's and the grading
rules': DDL there, statements here.

**A snapshot is a cut-line, not a copy.** §36 draws `observations` hanging off
it, and the obvious reading is a membership table — one row per price per
snapshot. Two arguments against, and the second is the decisive one:

* It carries no information. `market_observations` is append-only and
  `created_at` records when a row *landed*, so "this provider's observations
  stored at or before `generated_at`" is a set that cannot change afterwards.
  Writing it out would be tens of thousands of rows a day restating a
  `WHERE` clause.
* **It would be wrong.** ADR 0006's daily refresh covers 49,399 cards at a
  quarter of the provider's quota, and any run may cover fewer — rate limits,
  a partial failure, a provider that has no price for a card today. A snapshot
  built from "the rows this run wrote" would therefore hold today's *coverage*
  rather than the market as of today, and every card the run did not reach
  would read as `market_data_unavailable` when a perfectly good price from
  yesterday is on file. A snapshot resolves the latest **known** price, not the
  latest fetched one.

**The cut is `created_at`, never `observed_at`.** A backfilled price is seen
long before it is stored, so a snapshot cut on when a price was *seen* could be
joined retroactively by a late arrival — and an immutable snapshot that resolves
differently on two readings of the same data is not immutable at all.
`market_observations.created_at` exists for this and says so.

**Rows become entities.** Nothing here returns a mapping or a `sa.Row`.
`MarketSnapshot` is constructed on the way out, and so is `PriceObservation` —
which is not merely tidiness: building one runs `validated_grade_key`, so a
stored grade a company does not issue is caught when it is read as well as when
it is written. `validated_grade_key` names this caller.

**Plain functions and no Protocol.** A snapshot is this repository's own record
of its own ingestion, not a replaceable external provider, so a port here would
be an interface with one implementation — `tcg_api.grading.rules` and
`tcg_api.analysis.sessions` are the precedents.

**Every function raises `MarketSnapshotUnavailable` on a driver failure**, and
that is one sentence rather than a per-function caveat on purpose: #54's
ingestion gets the same translation the market route does. This module shipped
without it, exactly as `grading/rules.py` did, until a route arrived — #56 is
that route. The translation itself is `database.execute`, hoisted there in the
same change: this would have been the fifth near-identical
`(SQLAlchemyError, OSError)` wrapper, which is what `rules.py::_one_or_none`'s
`ponytail:` comment said not to write.

Being a `ConnectionError` keeps the worker's behaviour unchanged: a failure
inside the claim still propagates, rolls the claim back and lets the task retry,
which is what `CatalogUnavailable` already does two lines above `current_snapshot`.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.card import CardReference
from tcg_domain.catalog import Card
from tcg_domain.confidence import Confidence
from tcg_domain.grade import Grade
from tcg_domain.money import Currency, Money
from tcg_market_data import InvalidMarketObservation, MarketSnapshot, PriceObservation

from tcg_api.database import execute
from tcg_api.market.tables import market_observations, market_providers, market_snapshots

__all__ = [
    "MarketSnapshotUnavailable",
    "current_snapshot",
    "generate_snapshot",
    "get_snapshot",
    "resolve_prices",
]

_UNAVAILABLE: Final = "The market data store could not be reached."


class MarketSnapshotUnavailable(ConnectionError):
    """The snapshot store could not be read or written.

    A `ConnectionError` for the reason `CatalogUnavailable` and
    `AnalysisStoreUnavailable` are: the store being unreachable is a transport
    fact, and a caller that only cares about that can catch the builtin.

    **Not spec §66's `market_data_unavailable`.** That code means there is no
    usable price; this means the question could not be asked. `errors.py` states
    the distinction and the economic engine relies on it.
    """


_SNAPSHOT_COLUMNS: Final = (
    market_snapshots.c.id,
    market_snapshots.c.provider_id,
    market_snapshots.c.generated_at,
    market_snapshots.c.data_version,
)

_SELECT: Final = sa.select(*_SNAPSHOT_COLUMNS)

#: The latest price per `(grading_company, grade)`, which is what a snapshot
#: resolves to for one card.
#:
#: `DISTINCT ON` rather than a windowed `row_number()`: this is a top-one pick,
#: not a computed column, so there is nothing for a window's *value* to be —
#: `grading/rules.py` reaches for `lead()` because it needs one. PostgreSQL-only,
#: which this schema already is.
#:
#: **The ordering is total, and `id` is what makes it so.** Two rows tying under
#: a partial key could come back in either order, and `PriceObservation.history_key`
#: already names this module's problem: that would make an immutable snapshot
#: resolve differently on two readings of the same data. `observed_at DESC` first
#: because the newest price is the price; `created_at DESC` next so a correction
#: supersedes what it corrects, which is what "a corrected price is a new
#: observation" means at the read end; `id DESC` last because it is unique.
#:
#: Raw rows need no special case. `grading_company IS NULL` compares equal to
#: itself under `DISTINCT ON`, so every raw price for a card falls into one group
#: and yields one entry. `NULLS FIRST` puts raw ahead of graded, matching
#: `history_key`'s own ordering.
#:
#: ponytail: no index for the `provider_id` and `created_at` filters, and #50's
#: note about adding one for `provider_id` is deliberately still unheeded. Both
#: filter a set `ix_market_observations_card_company_grade_observed_at` has
#: already bounded to one card's history — a year of daily ingestion is ~365 rows
#: per key — and `card_id` is far the more selective column.
_PRICES: Final = (
    sa.select(
        market_observations.c.grading_company,
        market_observations.c.grade,
        market_observations.c.price,
        market_observations.c.currency,
        market_observations.c.confidence,
        market_observations.c.observed_at,
        market_providers.c.slug,
    )
    .join(market_providers, market_observations.c.provider_id == market_providers.c.id)
    .distinct(market_observations.c.grading_company, market_observations.c.grade)
    .order_by(
        market_observations.c.grading_company.asc().nulls_first(),
        market_observations.c.grade.asc(),
        market_observations.c.observed_at.desc(),
        market_observations.c.created_at.desc(),
        market_observations.c.id.desc(),
    )
)


def _entity(row: sa.Row[Any]) -> MarketSnapshot:
    return MarketSnapshot(
        id=row.id,
        provider=row.provider_id,
        generated_at=row.generated_at,
        data_version=row.data_version,
    )


def _observation(row: sa.Row[Any], card: CardReference) -> PriceObservation:
    # `market_observations.currency` admits any ISO 4217 code, deliberately —
    # #50's argument is that an observation records what the provider actually
    # said, and ADR 0006's provider prices in USD. `tcg_domain.money.Currency`
    # models only SGD, because V1 reports SGD and #53 owns the conversion. So a
    # row can, in principle, hold a currency this type cannot carry. Nothing has
    # ingested yet, so no such row exists; when one does, #53 is the milestone
    # that adds the member and the conversion. Refused with a message that says
    # so, rather than with the enum's own `ValueError`.
    try:
        currency = Currency(row.currency)
    except ValueError as error:
        raise InvalidMarketObservation(
            f"{row.currency} is stored but not modelled by tcg_domain.money.Currency; "
            "normalization owns the conversion"
        ) from error
    return PriceObservation(
        card=card,
        price=Money(amount=row.price, currency=currency),
        observed_at=row.observed_at,
        confidence=Confidence(row.confidence),
        provider=row.slug,
        grading_company=row.grading_company,
        grade=None if row.grade is None else Grade.parse(row.grade),
    )


async def generate_snapshot(db: AsyncSession, *, provider_id: UUID) -> MarketSnapshot:
    """Cut a new snapshot of `provider_id`'s prices and return it.

    **Call this inside the transaction that wrote the run's observations.** That
    is not a convenience: `generated_at` and `market_observations.created_at`
    both default to `now()`, which is transaction-start time, so the run's own
    rows carry exactly this snapshot's `generated_at` and the `<=` in
    :func:`resolve_prices` is what includes them. Generating afterwards, in a
    second transaction, would still work — it would simply cut later — but
    generating *before* the inserts would produce a snapshot of the day before
    the work.

    There is no `generated_at` parameter and no `data_version` one. The database
    writes the first and generates the second from it, so a caller cannot
    backdate a cut past prices that had already landed, nor name a version that
    disagrees with when it was cut.

    Does not commit; the caller owns the transaction, as
    `analysis.sessions.record_reproducibility` does.

    ponytail: the cut-line is sound only while there is one ingestion writer.
    `created_at` is transaction-start time, so a *second* ingestion transaction
    that began before this `generated_at` and commits after it would join a
    snapshot already generated. V1 runs one daily worker, so the precondition
    does not arise; if ingestion ever runs concurrently, serialise it on a
    transaction advisory lock taken before its first INSERT — not here, which is
    already too late to help.
    """
    statement = (
        sa.insert(market_snapshots)
        .values(id=uuid4(), provider_id=provider_id)
        .returning(*_SNAPSHOT_COLUMNS)
    )
    result = await execute(
        db, statement, unavailable=MarketSnapshotUnavailable, message=_UNAVAILABLE
    )
    return _entity(result.one())


async def current_snapshot(db: AsyncSession) -> MarketSnapshot | None:
    """The most recently generated snapshot, or `None` if none has been.

    `None` is the answer through V1 and is a fact rather than a gap: no
    `market_providers` row exists, because ADR 0006 gates commercial use on a
    subscription that is not yet active, so nothing has ingested and there is
    nothing to snapshot. An analysis records the absence rather than inventing a
    snapshot, exactly as it does for the card database version.

    Takes no provider argument. ADR 0006 selects one provider for V1, so "the
    current snapshot" is unambiguous; the milestone that ingests from two adds
    the parameter, which is the precedent `record_reproducibility` set by
    refusing a `grading_rules_version` argument until rules existed.

    Ordered by `(generated_at DESC, id DESC)` rather than by `generated_at`
    alone, so that two snapshots cut in the same instant still resolve to one
    answer on every reading.
    """
    statement = _SELECT.order_by(
        market_snapshots.c.generated_at.desc(),
        market_snapshots.c.id.desc(),
    ).limit(1)
    result = await execute(
        db, statement, unavailable=MarketSnapshotUnavailable, message=_UNAVAILABLE
    )
    row = result.one_or_none()
    return None if row is None else _entity(row)


async def get_snapshot(db: AsyncSession, snapshot_id: UUID) -> MarketSnapshot | None:
    """One snapshot by its identifier, or `None` if it was never generated.

    This is §57's reproducibility record read back: an analysis holds the
    identifier in `analyses.market_snapshot_id` and resolves the exact cut here,
    however many ingestion runs have happened since.
    """
    statement = _SELECT.where(market_snapshots.c.id == snapshot_id)
    result = await execute(
        db, statement, unavailable=MarketSnapshotUnavailable, message=_UNAVAILABLE
    )
    row = result.one_or_none()
    return None if row is None else _entity(row)


async def resolve_prices(
    db: AsyncSession, snapshot: MarketSnapshot, card: Card
) -> tuple[PriceObservation, ...]:
    """Every price `snapshot` holds for `card` — one per `(company, grade)`.

    The latest observation per key, from this snapshot's provider, stored at or
    before its `generated_at`. Because `market_observations` is append-only and
    the cut is on `created_at`, two calls a year apart return the same prices;
    that is the whole of §36's reproducibility claim, and the module docstring
    gives the argument.

    Raw first, then each graded key. An empty result is a legitimate answer — a
    card nobody has priced yet — and is `market_data_unavailable` rather than an
    error; #55 owns how that reaches a user, and a zero price is emphatically
    not the same thing.

    Takes a `Card` rather than a card id because a `PriceObservation` carries a
    `CardReference` and `Card.reference` already is one: passing the id would
    mean joining `cards` and `sets` to rebuild an identity every caller already
    holds. Both eventual callers — the market endpoint and the economic engine —
    hold the card.

    **Never calls a provider** (spec §37). The prices were ingested out of band;
    that is the point of a snapshot.
    """
    statement = _PRICES.where(
        market_observations.c.card_id == card.id,
        market_observations.c.provider_id == snapshot.provider,
        market_observations.c.created_at <= snapshot.generated_at,
    )
    result = await execute(
        db, statement, unavailable=MarketSnapshotUnavailable, message=_UNAVAILABLE
    )
    reference = card.reference
    return tuple(_observation(row, reference) for row in result)
