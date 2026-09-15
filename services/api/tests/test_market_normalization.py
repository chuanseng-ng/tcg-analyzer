"""Integration tests for writing normalized quotes — issue #53.

`packages/market-data`'s tests prove what `normalize` decides. These prove what
reaches PostgreSQL: a valid quote becomes one observation carrying its SGD
price and the rate that produced it, everything else becomes one quarantine row
with its reason, unmapped identifiers come back in the report, and a snapshot
cut in the same transaction resolves the SGD figure.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tcg_api.catalog.cards import CARD_SELECT, card_entity
from tcg_api.catalog.tables import card_external_ids, cards, sets
from tcg_api.market.exchange_rates import record_exchange_rate
from tcg_api.market.normalization import NormalizationReport, record_quotes
from tcg_api.market.snapshots import generate_snapshot, resolve_prices
from tcg_api.market.tables import (
    exchange_rates,
    market_observations,
    market_providers,
    market_quarantine,
)
from tcg_domain.money import Money
from tcg_market_data import ProviderQuote, QuarantineReason

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
CARD_ID = uuid.UUID("33333333-3333-5333-8333-333333333333")
REVERSE_ID = uuid.UUID("33333333-3333-5333-8333-444444444444")
PROVIDER_ID = uuid.UUID("66666666-6666-5666-8666-666666666666")
PROVIDER = "examplesource"

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
SEEN_AT = NOW - timedelta(hours=1)


def run(work: Callable[[AsyncSession], Awaitable[Any]], *, commit: bool = True) -> Any:
    """Run one coroutine factory in one transaction, committing unless told not to."""

    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with AsyncSession(engine) as session:
                result = await work(session)
                if commit:
                    await session.commit()
                else:
                    await session.rollback()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def rows(table: sa.Table) -> list[Any]:
    return run(lambda session: _all(session, table))


async def _all(session: AsyncSession, table: sa.Table) -> list[Any]:
    return list(await session.execute(sa.select(table)))


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
                "TRUNCATE market_snapshots, market_observations, market_quarantine, "
                "exchange_rates, market_providers, images, analyses, analysis_sessions, "
                "card_external_ids, cards, sets RESTART IDENTITY CASCADE"
            )
        )

    async def seed(session: AsyncSession) -> None:
        await session.execute(
            sa.insert(sets).values(
                id=SET_ID, game="pokemon", language="en", set_code="BS", name="Base Set"
            )
        )
        for card_id, variant in ((CARD_ID, "unlimited-holo"), (REVERSE_ID, "reverse-holo")):
            await session.execute(
                sa.insert(cards).values(
                    id=card_id,
                    game="pokemon",
                    language="en",
                    set_id=SET_ID,
                    card_number="4/102",
                    name="Charizard",
                    variant=variant,
                )
            )
        await session.execute(
            sa.insert(card_external_ids),
            [
                {
                    "id": uuid.uuid4(),
                    "card_id": CARD_ID,
                    "provider": PROVIDER,
                    "external_id": "bs-4",
                },
                # One identifier naming two variants: legal, and never guessed between.
                {
                    "id": uuid.uuid4(),
                    "card_id": CARD_ID,
                    "provider": PROVIDER,
                    "external_id": "bs-4x",
                },
                {
                    "id": uuid.uuid4(),
                    "card_id": REVERSE_ID,
                    "provider": PROVIDER,
                    "external_id": "bs-4x",
                },
                # Another provider's identifier must not map this provider's quote.
                {
                    "id": uuid.uuid4(),
                    "card_id": CARD_ID,
                    "provider": "tcgdex",
                    "external_id": "base1-4",
                },
            ],
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


def rate(as_of: date, value: str = "1.3421", base: str = "USD") -> uuid.UUID:
    async def work(session: AsyncSession) -> uuid.UUID:
        connection = await session.connection()
        return await record_exchange_rate(
            connection,
            base_currency=base,
            rate=Decimal(value),
            as_of=as_of,
            source_reference="https://example.test/rates",
        )

    return run(work)


def a_quote(**overrides: Any) -> ProviderQuote:
    fields: dict[str, Any] = {
        "external_id": "bs-4",
        "amount": "100.00",
        "currency": "USD",
        "observed_at": SEEN_AT,
        "confidence": 0.8,
        "metadata": {"sales": 12},
    }
    fields.update(overrides)
    return ProviderQuote(**fields)


def record(*quotes: ProviderQuote, commit: bool = True) -> NormalizationReport:
    return run(
        lambda session: record_quotes(
            session, provider_id=PROVIDER_ID, provider=PROVIDER, quotes=quotes, now=NOW
        ),
        commit=commit,
    )


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------
def test_a_usd_quote_is_stored_with_its_sgd_price_and_rate() -> None:
    rate_id = rate(NOW.date())

    report = record(a_quote())

    [row] = rows(market_observations)
    assert (row.currency, row.price, row.price_sgd) == ("USD", Decimal("100.00"), Decimal("134.21"))
    assert row.exchange_rate_id == rate_id
    assert row.card_id == CARD_ID
    assert row.provider_id == PROVIDER_ID
    assert row.metadata == {"sales": 12}
    assert report.stored == 1
    assert report.quarantined == {}


def test_an_sgd_quote_needs_no_rate() -> None:
    record(a_quote(currency="SGD", amount="42.00"))

    [row] = rows(market_observations)
    assert (row.price, row.price_sgd, row.exchange_rate_id) == (
        Decimal("42.00"),
        Decimal("42.00"),
        None,
    )


def test_the_latest_rate_on_or_before_the_observation_is_used() -> None:
    older = rate(NOW.date() - timedelta(days=3), "1.30")
    rate(NOW.date() - timedelta(days=1), "1.34")
    rate(NOW.date() + timedelta(days=1), "9.99")  # after the observation: never used

    record(a_quote(observed_at=NOW - timedelta(days=1)))

    [row] = rows(market_observations)
    assert row.price_sgd == Decimal("134.00")
    assert row.exchange_rate_id != older


def test_a_graded_quote_is_stored_under_its_company_and_grade() -> None:
    rate(NOW.date())

    record(a_quote(grading_company="bgs", grade="9.5"))

    [row] = rows(market_observations)
    assert (row.market_type, row.grading_company, row.grade) == ("graded", "bgs", "9.5")


# ---------------------------------------------------------------------------
# What is quarantined, and what is reported
# ---------------------------------------------------------------------------
def test_an_unmapped_identifier_is_quarantined_and_reported() -> None:
    rate(NOW.date())

    report = record(a_quote(external_id="base1-4"), a_quote(external_id="nope"))

    assert rows(market_observations) == []
    assert sorted(row.external_id for row in rows(market_quarantine)) == ["base1-4", "nope"]
    assert {row.reason for row in rows(market_quarantine)} == {"unmapped_card"}
    assert report.unmapped_external_ids == ("base1-4", "nope")
    assert report.quarantined == {QuarantineReason.UNMAPPED_CARD: 2}


def test_an_identifier_naming_two_cards_is_quarantined() -> None:
    rate(NOW.date())

    record(a_quote(external_id="bs-4x"))

    [row] = rows(market_quarantine)
    assert row.reason == "ambiguous_card"


def test_a_quote_with_no_rate_is_quarantined_with_what_the_provider_said() -> None:
    record(a_quote(amount=Decimal("100.00")))

    [row] = rows(market_quarantine)
    assert row.reason == "no_exchange_rate"
    assert row.provider_id == PROVIDER_ID
    assert row.detail
    assert row.record["amount"] == "100.00"
    assert row.record["observed_at"] == SEEN_AT.isoformat()


def test_a_non_finite_confidence_still_reaches_the_quarantine() -> None:
    """JSONB refuses NaN, so one such quote would otherwise sink the whole batch."""
    rate(NOW.date())

    report = record(a_quote(confidence=math.nan), a_quote())

    assert report.stored == 1
    [row] = rows(market_quarantine)
    assert row.reason == "invalid_confidence"
    assert row.record["confidence"] == "nan"


def test_stored_and_quarantined_quotes_are_counted_together() -> None:
    rate(NOW.date())

    report = record(a_quote(), a_quote(amount="-1"), a_quote(grading_company="psa", grade="9.5"))

    assert report.stored == 1
    assert report.quarantined == {
        QuarantineReason.INVALID_PRICE: 1,
        QuarantineReason.UNSUPPORTED_GRADE: 1,
    }


def test_nothing_is_committed_by_recording() -> None:
    """#54 owns the transaction, and cuts its snapshot inside it."""
    rate(NOW.date())

    record(a_quote(), a_quote(external_id="nope"), commit=False)

    assert rows(market_observations) == []
    assert rows(market_quarantine) == []


def test_no_quotes_is_an_empty_report() -> None:
    assert record() == NormalizationReport(stored=0, quarantined={}, unmapped_external_ids=())


# ---------------------------------------------------------------------------
# A snapshot resolves the SGD figure
# ---------------------------------------------------------------------------
def test_a_snapshot_cut_in_the_same_transaction_resolves_the_sgd_price() -> None:
    rate(NOW.date())

    async def ingest_and_resolve(session: AsyncSession) -> tuple[Money, ...]:
        await record_quotes(
            session, provider_id=PROVIDER_ID, provider=PROVIDER, quotes=(a_quote(),), now=NOW
        )
        snapshot = await generate_snapshot(session, provider_id=PROVIDER_ID)
        card = card_entity((await session.execute(CARD_SELECT.where(cards.c.id == CARD_ID))).one())
        return tuple(
            observation.price for observation in await resolve_prices(session, snapshot, card)
        )

    assert run(ingest_and_resolve) == (Money.of("134.21"),)


# ---------------------------------------------------------------------------
# Recording a rate
# ---------------------------------------------------------------------------
def test_a_recorded_rate_is_stored_exactly() -> None:
    rate(date(2026, 9, 15), "1.34215678")

    [row] = rows(exchange_rates)
    assert (row.base_currency, row.quote_currency, row.rate) == (
        "USD",
        "SGD",
        Decimal("1.34215678"),
    )
    assert row.source_reference == "https://example.test/rates"
