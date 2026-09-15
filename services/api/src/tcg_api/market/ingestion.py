"""The daily market ingestion run — issue #54, spec §37.

`scheduler → worker → provider → normalization → validation → Postgres`. The
provider is a `QuoteSource` (#52 implements it); normalization and validation are
`record_quotes` (#53); the cut is `generate_snapshot` (#51). This module is the
run around them, and **no request path imports it**: a user's analysis reads a
snapshot, never a provider (`test_import_purity.py` holds that line).

Four steps, and the transaction boundaries are the design:

1. **Claim**, committed on its own. A run left `running` past `STALE_AFTER` is
   marked `abandoned` first; then one `running` row is inserted, or none if
   another run holds the partial unique index — the database, not the schedule,
   keeps #51's one-writer precondition. No source, or no `market_providers` row
   for it, is a `skipped` run: through V1 until #52, that is every run.
2. **Fetch**, with no transaction open. A full catalog is ~99 minutes at ADR
   0006's quota, and a transaction held that long pins every row version the
   database would otherwise vacuum. Batches run one at a time; a batch the
   provider never answers after `FETCH_ATTEMPTS` is counted and skipped. **A
   partial run is still a run** — all-or-nothing would let one bad card leave the
   product with no prices at all.
3. **Write**, in one transaction: `record_quotes`, then `generate_snapshot`,
   then the run's outcome. The snapshot's `<=` against a shared `now()` is what
   includes the run's own rows, so the two must not be split.
4. **Report**. `market.prices_ingested` (the name `docs/observability.md`
   reserved), then one `market.ingestion_alert` per reason. **Coverage is
   alerted on, not only exceptions**: a run that stores far fewer prices than
   the last one "succeeded" in every other sense.

Re-running is safe. A failed run's write rolled back, and a repeated one appends
observations whose snapshot resolves the same latest prices.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import batched
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tcg_market_data import MarketProviderUnavailable, ProviderQuote, QuoteSource

from tcg_api.catalog.tables import card_external_ids
from tcg_api.database import execute
from tcg_api.market.normalization import MarketNormalizationUnavailable, record_quotes
from tcg_api.market.snapshots import MarketSnapshotUnavailable, generate_snapshot
from tcg_api.market.tables import market_ingestion_runs, market_providers

__all__ = [
    "BACKOFF_MAX_SECONDS",
    "BACKOFF_SECONDS",
    "BATCH_SIZE",
    "COVERAGE_ALERT_RATIO",
    "FETCH_ATTEMPTS",
    "STALE_AFTER",
    "IngestionRun",
    "MarketIngestionUnavailable",
    "get_quote_source",
    "ingest",
]

logger = structlog.get_logger(__name__)

_UNAVAILABLE: Final = "The market data store could not be reached."

#: Provider identifiers per `fetch`.
#:
#: ponytail: a round number. ADR 0006 assumes no batching at all, so #52 sets
#: this from what its provider actually accepts per request.
BATCH_SIZE: Final = 100

#: Attempts per batch before it is counted failed and the run moves on.
FETCH_ATTEMPTS: Final = 3

#: The backoff's base and ceiling, in seconds: 2, then 4. No jitter, because one
#: run is one caller — jitter is for a herd, and there is no herd.
BACKOFF_SECONDS: Final = 2.0
BACKOFF_MAX_SECONDS: Final = 60.0

#: How long a `running` row may sit before the next run marks it `abandoned`.
#: A killed worker writes nothing, so this is what eventually reaches its row.
#:
#: ponytail: a quarter-day against a ~99-minute full run. #52's first real runs
#: measure the duration this should be a multiple of.
STALE_AFTER: Final = timedelta(hours=6)

#: A completed run storing fewer than this share of the previous completed run's
#: observations is alerted on as lost coverage.
#:
#: ponytail: a guess with no run history behind it. #52 recalibrates it, with
#: normalization's bounds, from its first real runs.
COVERAGE_ALERT_RATIO: Final = 0.8


class MarketIngestionUnavailable(ConnectionError):
    """The market store could not be reached while claiming or reading for a run.

    **Not spec §66's `market_data_unavailable`**, and a `ConnectionError` for the
    reason every other store's is.
    """


@dataclass(frozen=True, slots=True)
class IngestionRun:
    """One run's outcome, as its `market_ingestion_runs` row records it."""

    id: UUID
    status: str
    failure_reason: str | None = None
    batches: int = 0
    batches_failed: int = 0
    stored: int = 0
    quarantined: Mapping[str, int] = field(default_factory=dict)
    unmapped: int = 0
    snapshot_id: UUID | None = None


def get_quote_source() -> QuoteSource | None:
    """The source the scheduled run ingests from, or `None` if there is none.

    ponytail: `None` through V1. ADR 0006 forbids ingesting before the
    subscription is active, so there is nothing to return; #52 builds its
    adapter here from settings, and every run until then records `skipped`.
    """
    return None


async def ingest(
    session_factory: async_sessionmaker[AsyncSession],
    source: QuoteSource | None,
    *,
    now: datetime,
    batch_size: int = BATCH_SIZE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> IngestionRun | None:
    """Run one ingestion, returning its outcome, or `None` if another run holds the claim.

    Args:
        session_factory: Where each step's transaction comes from.
        source: The provider to read, or `None` when none is configured.
        now: The moment quotes are judged against, timezone-aware.
        batch_size: Identifiers per `fetch`.
        sleep: How the backoff waits; a parameter so a test need not.

    Raises:
        MarketIngestionUnavailable: If the store could not be reached to claim
            the run. A store failure after the claim is recorded as a `failed`
            run instead.
    """
    started = time.perf_counter()
    claimed = await _claim(session_factory, source)
    if claimed is None or isinstance(claimed, IngestionRun):
        return claimed
    run_id, provider_id, source = claimed

    identifiers = await _identifiers(session_factory, source.provider)
    quotes: list[ProviderQuote] = []
    batches = failed = 0
    for batch in batched(identifiers, batch_size):
        batches += 1
        fetched = await _fetch(source, batch, sleep)
        if fetched is None:
            failed += 1
        else:
            quotes.extend(fetched)

    if batches and failed == batches:
        result = await _fail(session_factory, run_id, "provider_unavailable", batches, failed)
    else:
        try:
            result = await _write(
                session_factory, run_id, provider_id, source.provider, quotes, now, batches, failed
            )
        except (MarketNormalizationUnavailable, MarketSnapshotUnavailable):
            result = await _fail(session_factory, run_id, "store_unreachable", batches, failed)

    previous = await _previous_stored(session_factory, run_id)
    _report(result, source.provider, previous, round((time.perf_counter() - started) * 1000))
    return result


async def _claim(
    session_factory: async_sessionmaker[AsyncSession], source: QuoteSource | None
) -> IngestionRun | tuple[UUID, UUID, QuoteSource] | None:
    """Abandon a stale run, then take the one `running` row or record a skip.

    A claim carries its source back, so the caller holds a source that is known
    not to be `None` without asserting it.
    """
    async with session_factory() as db:
        abandoned = await _run(
            db,
            sa.update(market_ingestion_runs)
            .where(
                market_ingestion_runs.c.status == "running",
                market_ingestion_runs.c.started_at < sa.func.now() - STALE_AFTER,
            )
            .values(status="failed", failure_reason="abandoned", completed_at=sa.func.now())
            .returning(market_ingestion_runs.c.id),
        )
        for row in abandoned:
            logger.error("market.ingestion_alert", run_id=str(row.id), reason="run_abandoned")

        provider_id = None if source is None else await _provider_id(db, source.provider)
        if source is None or provider_id is None:
            skipped = IngestionRun(id=uuid4(), status="skipped")
            await _run(
                db,
                sa.insert(market_ingestion_runs).values(
                    id=skipped.id, status="skipped", completed_at=sa.func.now()
                ),
            )
            await db.commit()
            logger.info(
                "market.ingestion_skipped",
                run_id=str(skipped.id),
                reason="no_source" if source is None else "no_provider_row",
            )
            return skipped

        run_id = uuid4()
        # `ON CONFLICT DO NOTHING` against the partial index, so a run already
        # holding the claim is a returned nothing rather than an exception.
        taken = await _run(
            db,
            postgresql.insert(market_ingestion_runs)
            .values(id=run_id, provider_id=provider_id, status="running")
            .on_conflict_do_nothing(
                index_elements=["status"], index_where=sa.text("status = 'running'")
            )
            .returning(market_ingestion_runs.c.id),
        )
        claimed = taken.one_or_none() is not None
        await db.commit()
    if not claimed:
        logger.warning("market.ingestion_already_running", provider=source.provider)
        return None
    return run_id, provider_id, source


async def _provider_id(db: AsyncSession, slug: str) -> UUID | None:
    """The provider row a run ingests under.

    A slug carries one row per published version of its terms, so the most
    recently verified one — the terms the project last read — is the one used.
    """
    result = await _run(
        db,
        sa.select(market_providers.c.id)
        .where(market_providers.c.slug == slug)
        .order_by(market_providers.c.verified_on.desc(), market_providers.c.id.desc())
        .limit(1),
    )
    found = result.scalar_one_or_none()
    return None if found is None else UUID(str(found))


async def _identifiers(
    session_factory: async_sessionmaker[AsyncSession], provider: str
) -> list[str]:
    """Every identifier the catalog carries for `provider`, once each, in order.

    Sorted, so a run asks in the same order every day and a partial failure is
    comparable between runs.
    """
    async with session_factory() as db:
        result = await _run(
            db,
            sa.select(card_external_ids.c.external_id)
            .where(card_external_ids.c.provider == provider)
            .distinct()
            .order_by(card_external_ids.c.external_id),
        )
        return [str(identifier) for identifier in result.scalars()]


async def _fetch(
    source: QuoteSource,
    batch: Sequence[str],
    sleep: Callable[[float], Awaitable[None]],
) -> Sequence[ProviderQuote] | None:
    """One batch, retried with backoff; `None` once every attempt has failed."""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return await source.fetch(batch)
        except MarketProviderUnavailable:
            if attempt == FETCH_ATTEMPTS - 1:
                logger.warning(
                    "market.ingestion_batch_failed",
                    provider=source.provider,
                    identifiers=len(batch),
                    attempts=FETCH_ATTEMPTS,
                )
                return None
            await sleep(min(BACKOFF_MAX_SECONDS, BACKOFF_SECONDS * 2**attempt))
    return None  # unreachable: the loop returns on its last attempt


async def _write(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    provider_id: UUID,
    provider: str,
    quotes: Sequence[ProviderQuote],
    now: datetime,
    batches: int,
    failed: int,
) -> IngestionRun:
    """Record the quotes, cut the snapshot and complete the run — one transaction."""
    async with session_factory() as db:
        report = await record_quotes(
            db, provider_id=provider_id, provider=provider, quotes=quotes, now=now
        )
        snapshot = await generate_snapshot(db, provider_id=provider_id)
        result = IngestionRun(
            id=run_id,
            status="completed",
            batches=batches,
            batches_failed=failed,
            stored=report.stored,
            quarantined={reason.value: count for reason, count in report.quarantined.items()},
            unmapped=len(report.unmapped_external_ids),
            snapshot_id=snapshot.id,
        )
        await _finish(db, result)
        await db.commit()
        return result


async def _fail(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    reason: str,
    batches: int,
    failed: int,
) -> IngestionRun:
    """Record a failed run in a fresh transaction; the write's, if any, rolled back."""
    result = IngestionRun(
        id=run_id, status="failed", failure_reason=reason, batches=batches, batches_failed=failed
    )
    async with session_factory() as db:
        await _finish(db, result)
        await db.commit()
    return result


async def _finish(db: AsyncSession, result: IngestionRun) -> None:
    await _run(
        db,
        sa.update(market_ingestion_runs)
        .where(market_ingestion_runs.c.id == result.id)
        .values(
            status=result.status,
            failure_reason=result.failure_reason,
            completed_at=sa.func.now(),
            batches=result.batches,
            batches_failed=result.batches_failed,
            stored=result.stored,
            quarantined=dict(result.quarantined),
            unmapped=result.unmapped,
            snapshot_id=result.snapshot_id,
        ),
    )


async def _previous_stored(
    session_factory: async_sessionmaker[AsyncSession], run_id: UUID
) -> int | None:
    """What the last completed run before this one stored, if there was one."""
    async with session_factory() as db:
        result = await _run(
            db,
            sa.select(market_ingestion_runs.c.stored)
            .where(
                market_ingestion_runs.c.status == "completed",
                market_ingestion_runs.c.id != run_id,
            )
            .order_by(market_ingestion_runs.c.started_at.desc())
            .limit(1),
        )
        found = result.scalar_one_or_none()
        return None if found is None else int(found)


def _report(result: IngestionRun, provider: str, previous: int | None, duration_ms: int) -> None:
    """Counts only — a quote's content is the provider's data (ADR 0006)."""
    logger.info(
        "market.prices_ingested",
        run_id=str(result.id),
        provider=provider,
        status=result.status,
        duration_ms=duration_ms,
        batches=result.batches,
        batches_failed=result.batches_failed,
        stored=result.stored,
        quarantined=dict(result.quarantined),
        unmapped=result.unmapped,
    )
    reasons: list[str] = []
    if result.status == "failed":
        reasons.append("run_failed")
    if result.batches_failed:
        reasons.append("batches_failed")
    if previous is not None and result.stored < COVERAGE_ALERT_RATIO * previous:
        reasons.append("coverage_dropped")
    if result.unmapped:
        reasons.append("unmapped_cards")
    for reason in reasons:
        logger.error(
            "market.ingestion_alert",
            run_id=str(result.id),
            provider=provider,
            reason=reason,
            stored=result.stored,
            previous_stored=previous,
            batches_failed=result.batches_failed,
            unmapped=result.unmapped,
            failure_reason=result.failure_reason,
        )


async def _run(db: AsyncSession, statement: sa.Executable) -> sa.Result[Any]:
    return await execute(
        db, statement, unavailable=MarketIngestionUnavailable, message=_UNAVAILABLE
    )
