"""Integration tests for the daily market ingestion run — issue #54.

A fake `QuoteSource` stands in for #52's adapter, which does not exist yet. What
is proven here is everything around it: a run stores prices and cuts a snapshot
that resolves them, a partial failure still leaves a snapshot and says what
failed, a re-run is safe, only one run writes at a time, and a run that
"succeeds" while losing coverage is alerted on rather than believed.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import CapturingLogger
from tcg_api.catalog.cards import CARD_SELECT, card_entity
from tcg_api.catalog.tables import card_external_ids, cards, sets
from tcg_api.market import ingestion
from tcg_api.market.ingestion import IngestionRun, ingest
from tcg_api.market.normalization import MarketNormalizationUnavailable
from tcg_api.market.snapshots import get_snapshot, resolve_prices
from tcg_api.market.tables import market_ingestion_runs, market_providers, market_snapshots
from tcg_domain.money import Money
from tcg_market_data import MarketProviderUnavailable, ProviderQuote

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")
PROVIDER_ID = uuid.UUID("66666666-6666-5666-8666-666666666666")
PROVIDER = "examplesource"
CARD_IDS = {
    "bs-1": uuid.UUID("33333333-3333-5333-8333-000000000001"),
    "bs-2": uuid.UUID("33333333-3333-5333-8333-000000000002"),
    "bs-3": uuid.UUID("33333333-3333-5333-8333-000000000003"),
}
PRICES = {"bs-1": "10.00", "bs-2": "20.00", "bs-3": "30.00"}

NOW = datetime.now(UTC)
SEEN_AT = NOW - timedelta(hours=1)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def run(work: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with AsyncSession(engine) as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def rows(table: sa.Table) -> list[Any]:
    async def work(session: AsyncSession) -> list[Any]:
        return list(await session.execute(sa.select(table)))

    return run(work)


@dataclass
class FakeSource:
    """#52's adapter, as far as a run can tell."""

    prices: dict[str, str] = field(default_factory=lambda: dict(PRICES))
    down_for: set[str] = field(default_factory=set)
    flaky: int = 0
    extra: tuple[str, ...] = ()
    provider: str = PROVIDER
    calls: list[tuple[str, ...]] = field(default_factory=list)
    in_flight: int = 0
    most_in_flight: int = 0

    async def fetch(self, external_ids: Sequence[str]) -> Sequence[ProviderQuote]:
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            self.calls.append(tuple(external_ids))
            if self.flaky:
                self.flaky -= 1
                raise MarketProviderUnavailable("the provider did not answer")
            if self.down_for & set(external_ids):
                raise MarketProviderUnavailable("the provider did not answer")
            # A price it does not hold is no quote at all, as a real provider's
            # gap would be; `extra` is an identifier it answers for anyway.
            return tuple(
                ProviderQuote(
                    external_id=identifier,
                    amount=self.prices.get(identifier, "1.00"),
                    currency="SGD",
                    observed_at=SEEN_AT,
                    confidence=0.8,
                )
                for identifier in (*external_ids, *self.extra)
                if identifier in self.prices or identifier in self.extra
            )
        finally:
            self.in_flight -= 1


@dataclass
class Sleeps:
    waits: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def ingesting(
    source: FakeSource | None, *, batch_size: int = 1, sleeps: Sleeps | None = None
) -> IngestionRun | None:
    async def scenario() -> IngestionRun | None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            return await ingest(
                async_sessionmaker(engine, expire_on_commit=False),
                source,
                now=datetime.now(UTC),
                batch_size=batch_size,
                sleep=sleeps or Sleeps(),
            )
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def prices_in(snapshot_id: uuid.UUID, external_id: str) -> tuple[Money, ...]:
    async def work(session: AsyncSession) -> tuple[Money, ...]:
        snapshot = await get_snapshot(session, snapshot_id)
        assert snapshot is not None
        row = (await session.execute(CARD_SELECT.where(cards.c.id == CARD_IDS[external_id]))).one()
        return tuple(
            observation.price
            for observation in await resolve_prices(session, snapshot, card_entity(row))
        )

    return run(work)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def catalog_and_provider() -> Iterator[None]:
    async def truncate(session: AsyncSession) -> None:
        await session.execute(
            sa.text(
                "TRUNCATE market_ingestion_runs, market_snapshots, market_observations, "
                "market_quarantine, exchange_rates, market_providers, images, analyses, "
                "analysis_sessions, card_external_ids, cards, sets RESTART IDENTITY CASCADE"
            )
        )

    async def seed(session: AsyncSession) -> None:
        await session.execute(
            sa.insert(sets).values(
                id=SET_ID, game="pokemon", language="en", set_code="BS", name="Base Set"
            )
        )
        for number, (external_id, card_id) in enumerate(CARD_IDS.items(), start=1):
            await session.execute(
                sa.insert(cards).values(
                    id=card_id,
                    game="pokemon",
                    language="en",
                    set_id=SET_ID,
                    card_number=f"{number}/102",
                    name=f"Card {number}",
                    variant="unlimited",
                )
            )
            await session.execute(
                sa.insert(card_external_ids).values(
                    id=uuid.uuid4(), card_id=card_id, provider=PROVIDER, external_id=external_id
                )
            )
        await session.execute(
            sa.insert(market_providers).values(
                id=PROVIDER_ID,
                slug=PROVIDER,
                name="ExampleSource",
                license="Commercial use permitted; caching permitted.",
                commercial_use=True,
                terms_reference="https://example.test/terms",
                verified_on=date(2026, 9, 1),
            )
        )

    run(truncate)
    run(seed)
    yield
    run(truncate)


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> CapturingLogger:
    # The module's own logger, replaced, for `test_analysis_jobs.py`'s reason:
    # `capture_logs` does nothing to a logger another test has already cached.
    recorder = CapturingLogger()
    monkeypatch.setattr(ingestion, "logger", recorder)
    return recorder


def alert_reasons(recorder: CapturingLogger) -> list[str]:
    return [
        call.kwargs["reason"]
        for call in recorder.calls
        if call.args == ("market.ingestion_alert",) and call.method_name == "error"
    ]


# ---------------------------------------------------------------------------
# A run
# ---------------------------------------------------------------------------
def test_a_run_stores_prices_and_cuts_a_snapshot_that_resolves_them() -> None:
    result = ingesting(FakeSource())

    assert result is not None
    assert (result.status, result.stored, result.batches) == ("completed", 3, 3)
    assert result.snapshot_id is not None
    assert prices_in(result.snapshot_id, "bs-2") == (Money.of("20.00"),)
    [row] = rows(market_ingestion_runs)
    assert (row.status, row.stored, row.snapshot_id, row.provider_id) == (
        "completed",
        3,
        result.snapshot_id,
        PROVIDER_ID,
    )
    assert row.completed_at is not None


def test_every_mapped_identifier_is_asked_for_once_and_never_concurrently() -> None:
    source = FakeSource()

    ingesting(source, batch_size=2)

    assert source.calls == [("bs-1", "bs-2"), ("bs-3",)]
    assert source.most_in_flight == 1


def test_a_batch_the_provider_never_answers_still_leaves_a_snapshot() -> None:
    """All-or-nothing would let one bad card leave the product with no prices."""
    sleeps = Sleeps()

    result = ingesting(FakeSource(down_for={"bs-2"}), sleeps=sleeps)

    assert result is not None
    assert (result.status, result.batches, result.batches_failed, result.stored) == (
        "completed",
        3,
        1,
        2,
    )
    assert result.snapshot_id is not None
    assert prices_in(result.snapshot_id, "bs-1") == (Money.of("10.00"),)
    assert len(sleeps.waits) == ingestion.FETCH_ATTEMPTS - 1


def test_a_provider_that_recovers_is_retried_after_a_growing_backoff() -> None:
    sleeps = Sleeps()

    result = ingesting(FakeSource(flaky=2), batch_size=3, sleeps=sleeps)

    assert result is not None
    assert (result.status, result.batches_failed, result.stored) == ("completed", 0, 3)
    assert len(sleeps.waits) == 2
    assert 0 < sleeps.waits[0] < sleeps.waits[1] <= ingestion.BACKOFF_MAX_SECONDS


def test_a_run_the_provider_never_answers_fails_without_a_snapshot(
    alerts: CapturingLogger,
) -> None:
    result = ingesting(FakeSource(down_for=set(PRICES)))

    assert result is not None
    assert (result.status, result.failure_reason, result.snapshot_id) == (
        "failed",
        "provider_unavailable",
        None,
    )
    assert rows(market_snapshots) == []
    assert "run_failed" in alert_reasons(alerts)


def test_running_twice_is_safe() -> None:
    first = ingesting(FakeSource())
    second = ingesting(FakeSource())

    assert first is not None and second is not None
    assert first.snapshot_id is not None and second.snapshot_id is not None
    assert first.snapshot_id != second.snapshot_id
    assert prices_in(first.snapshot_id, "bs-3") == prices_in(second.snapshot_id, "bs-3")


# ---------------------------------------------------------------------------
# One writer
# ---------------------------------------------------------------------------
def test_a_run_steps_aside_while_another_is_running() -> None:
    run(
        lambda session: session.execute(
            sa.insert(market_ingestion_runs).values(
                id=uuid.uuid4(), provider_id=PROVIDER_ID, status="running"
            )
        )
    )
    source = FakeSource()

    assert ingesting(source) is None
    assert source.calls == []


def test_a_run_left_running_past_its_budget_is_abandoned() -> None:
    stale = uuid.uuid4()
    run(
        lambda session: session.execute(
            sa.insert(market_ingestion_runs).values(
                id=stale,
                provider_id=PROVIDER_ID,
                status="running",
                started_at=datetime.now(UTC) - ingestion.STALE_AFTER - timedelta(minutes=1),
            )
        )
    )

    result = ingesting(FakeSource())

    assert result is not None and result.status == "completed"
    [abandoned] = [row for row in rows(market_ingestion_runs) if row.id == stale]
    assert (abandoned.status, abandoned.failure_reason) == ("failed", "abandoned")


# ---------------------------------------------------------------------------
# Nothing to ingest from
# ---------------------------------------------------------------------------
def test_no_source_is_a_skipped_run() -> None:
    """Through V1 until #52: the tick runs, records that it had nothing, and stops."""
    result = ingesting(None)

    assert result is not None and result.status == "skipped"
    [row] = rows(market_ingestion_runs)
    assert (row.status, row.provider_id) == ("skipped", None)
    assert rows(market_snapshots) == []


def test_a_source_with_no_registered_provider_is_skipped() -> None:
    """ADR 0006: no row, no licence determination, no ingestion."""
    source = FakeSource(provider="unregistered")

    result = ingesting(source)

    assert result is not None and result.status == "skipped"
    assert source.calls == []


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
def test_a_run_that_loses_coverage_is_alerted_on(alerts: CapturingLogger) -> None:
    """The failure most likely to go unnoticed: a run that succeeds with half the cards."""
    ingesting(FakeSource())

    ingesting(FakeSource(prices={"bs-1": "10.00"}))

    assert "coverage_dropped" in alert_reasons(alerts)


def test_a_steady_run_raises_no_alert(alerts: CapturingLogger) -> None:
    ingesting(FakeSource())
    ingesting(FakeSource())

    assert alert_reasons(alerts) == []
    ingested = [call for call in alerts.calls if call.args == ("market.prices_ingested",)]
    assert len(ingested) == 2
    assert ingested[-1].kwargs["stored"] == 3
    assert ingested[-1].kwargs["duration_ms"] >= 0


def test_a_failed_batch_is_alerted_on(alerts: CapturingLogger) -> None:
    ingesting(FakeSource(down_for={"bs-2"}))

    assert "batches_failed" in alert_reasons(alerts)


def test_an_identifier_no_card_carries_is_alerted_on(alerts: CapturingLogger) -> None:
    result = ingesting(FakeSource(extra=("ghost",)), batch_size=3)

    assert result is not None and result.unmapped == 1
    assert "unmapped_cards" in alert_reasons(alerts)


def test_a_store_lost_mid_write_fails_the_run_and_writes_nothing(
    alerts: CapturingLogger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write rolls back whole: no half a run's prices, and no snapshot of them."""

    async def unreachable(*_: Any, **__: Any) -> None:
        raise MarketNormalizationUnavailable("The market data store could not be reached.")

    monkeypatch.setattr(ingestion, "record_quotes", unreachable)

    result = ingesting(FakeSource())

    assert result is not None
    assert (result.status, result.failure_reason, result.snapshot_id) == (
        "failed",
        "store_unreachable",
        None,
    )
    [row] = rows(market_ingestion_runs)
    assert (row.status, row.failure_reason) == ("failed", "store_unreachable")
    assert rows(market_snapshots) == []
    assert "run_failed" in alert_reasons(alerts)
