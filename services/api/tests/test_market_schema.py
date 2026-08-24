"""Integration tests for the market schema as PostgreSQL actually built it.

`test_market_tables.py` proves the schema was *declared* correctly. This proves
the migration built what was declared, and the guarantees only a real database
can demonstrate: `market_type` is generated rather than written and cannot be
made to disagree with `grading_company`; a price cannot be rewritten; a price
survives arithmetic without floating-point drift; and a card that has prices
cannot be removed from the catalog underneath them.

The refusal tests carry more load than they look like they do. Alembic compares
no triggers at all, so the drift guard in `test_catalog_schema.py` would not
notice a migration that never created one. Asking a real database to perform the
UPDATE is the only thing that would.

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
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.catalog.tables import cards, sets
from tcg_api.market.tables import market_observations, market_providers
from tcg_grading_companies import GradingCompany

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to inspect",
    ),
]


def alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def run_sync(work: Callable[[Connection], Any]) -> Any:
    """Run one synchronous callable against a fresh connection."""

    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(work)
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def query(statement: sa.Executable, parameters: Any = None) -> list[Any]:
    def work(connection: Connection) -> list[Any]:
        return list(connection.execute(statement, parameters))

    return run_sync(work)


def write(statements: list[tuple[sa.Executable, Any]]) -> None:
    """Execute `statements` in one transaction, letting failures propagate."""

    def work(connection: Connection) -> None:
        with connection.begin():
            for statement, parameters in statements:
                connection.execute(statement, parameters)

    run_sync(work)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Every test in this module reads the schema at `head`.

    `test_migrations.py` deliberately leaves the database at `base`, and pytest
    makes no promise about which module runs first.
    """
    alembic("upgrade", "head")


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    def truncate(connection: Connection) -> None:
        with connection.begin():
            # Child first, and the catalog too: `market_observations.card_id`
            # references `cards`, so a card left behind by another module would
            # survive into these tests. TRUNCATE also bypasses row-level
            # triggers, which is the only reason these tables can be emptied at
            # all now that an UPDATE is refused.
            connection.execute(
                sa.text(
                    "TRUNCATE market_observations, market_providers, "
                    "card_external_ids, cards, sets RESTART IDENTITY CASCADE"
                )
            )

    run_sync(truncate)
    yield
    run_sync(truncate)


# ---------------------------------------------------------------------------
# Fixture data. Built here rather than loaded from `database/seeds` so that a
# schema test fails for schema reasons and never because a seed changed.
# ---------------------------------------------------------------------------
SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")
CARD_ID = uuid.UUID("33333333-3333-5333-8333-333333333333")
PROVIDER_ID = uuid.UUID("66666666-6666-5666-8666-666666666666")

SEEN_AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
READ_ON = date(2026, 8, 24)


def seed_catalog() -> None:
    write(
        [
            (
                sa.insert(sets),
                {
                    "id": SET_ID,
                    "game": "pokemon",
                    "language": "en",
                    "set_code": "BS",
                    "name": "Base Set",
                },
            ),
            (
                sa.insert(cards),
                {
                    "id": CARD_ID,
                    "game": "pokemon",
                    "language": "en",
                    "set_id": SET_ID,
                    "card_number": "4/102",
                    "name": "Charizard",
                    "variant": "unlimited-holo",
                },
            ),
        ]
    )


def provider_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": PROVIDER_ID,
        "slug": "examplesource",
        "name": "ExampleSource",
        "version": None,
        "license": "Commercial use permitted on the Business plan; caching permitted.",
        "commercial_use": True,
        "terms_reference": "https://example.test/terms",
        "verified_on": READ_ON,
    }
    values.update(overrides)
    return values


def register(**overrides: Any) -> None:
    write([(sa.insert(market_providers), provider_values(**overrides))])


def observation_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "card_id": CARD_ID,
        "provider_id": PROVIDER_ID,
        "grading_company": None,
        "grade": None,
        "currency": "SGD",
        "price": Decimal("412.50"),
        "confidence": 0.8,
        "observed_at": SEEN_AT,
    }
    values.update(overrides)
    return values


def observe(**overrides: Any) -> uuid.UUID:
    values = observation_values(**overrides)
    write([(sa.insert(market_observations), values)])
    return uuid.UUID(str(values["id"]))


@pytest.fixture
def catalog_and_provider() -> None:
    seed_catalog()
    register()


# ---------------------------------------------------------------------------
# Spec §35 — `market_type` is derived, and cannot be made to disagree
# ---------------------------------------------------------------------------
def test_an_observation_with_no_company_is_raw(catalog_and_provider: None) -> None:
    observe()
    assert query(sa.select(market_observations.c.market_type))[0][0] == "raw"


def test_an_observation_with_a_company_and_grade_is_graded(catalog_and_provider: None) -> None:
    observe(grading_company="bgs", grade="9.5")
    assert query(sa.select(market_observations.c.market_type))[0][0] == "graded"


def test_the_market_type_cannot_be_written(catalog_and_provider: None) -> None:
    """A generated column refuses an explicit value, which is the whole point.

    `PriceObservation.market_type` is the only place the raw/graded rule is
    stated; an INSERT that could name this column would be a second statement of
    it, free to disagree.
    """
    with pytest.raises(DBAPIError, match="market_type"):
        write(
            [
                (
                    sa.text(
                        "INSERT INTO market_observations "
                        "(id, card_id, provider_id, market_type, currency, price, "
                        " confidence, observed_at) "
                        "VALUES (:id, :card, :provider, 'raw', 'SGD', 1.00, 0.5, :seen)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "card": CARD_ID,
                        "provider": PROVIDER_ID,
                        "seen": SEEN_AT,
                    },
                )
            ]
        )


def test_a_graded_observation_without_a_grade_is_refused(catalog_and_provider: None) -> None:
    with pytest.raises(IntegrityError, match="graded_rows_carry_a_company_and_a_grade"):
        observe(grading_company="psa")


def test_a_grade_without_a_company_is_refused(catalog_and_provider: None) -> None:
    with pytest.raises(IntegrityError, match="graded_rows_carry_a_company_and_a_grade"):
        observe(grade="10")


# ---------------------------------------------------------------------------
# Grades — text keys, half grades, and collapsed tails
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("company", list(GradingCompany))
def test_every_company_v1_ships_is_storable(
    company: GradingCompany, catalog_and_provider: None
) -> None:
    observe(grading_company=company.value, grade="9")
    stored = query(sa.select(market_observations.c.grading_company))
    assert stored[0][0] == company.value


def test_a_company_v1_does_not_ship_is_refused(catalog_and_provider: None) -> None:
    """The opposite call from `grading_rules.company`, asserted so nobody relaxes it."""
    with pytest.raises(IntegrityError, match="grading_company_is_a_supported_company"):
        observe(grading_company="cgc", grade="9")


@pytest.mark.parametrize("grade", ["1", "8.5", "9.5", "10", "7_or_lower", "9.5_or_higher"])
def test_a_grade_key_round_trips(grade: str, catalog_and_provider: None) -> None:
    """BGS is the company with a 9.5, and §24's collapsed tails are legal keys.

    Stored as text rather than a number, which is what makes both true at once.
    """
    observe(grading_company="bgs", grade=grade)
    assert query(sa.select(market_observations.c.grade))[0][0] == grade


@pytest.mark.parametrize("grade", ["10.5", "11", "9.25", "9.0"])
def test_a_key_that_is_not_a_grade_is_refused(grade: str, catalog_and_provider: None) -> None:
    """`9.0` is a real grade spelled wrong; storing both spellings would give it two keys."""
    with pytest.raises(IntegrityError, match="grade_is_a_grade_key"):
        observe(grading_company="bgs", grade=grade)


def test_a_psa_nine_and_a_half_is_stored_here_and_refused_upstream(
    catalog_and_provider: None,
) -> None:
    """The database knows the grammar; the port knows each company's scale.

    A per-company CHECK would make a fourth company, or a scale revision, cost a
    migration of this table. `tcg_market_data.validated_grade_key` refuses this
    pair before it can ever reach an INSERT, and neither guard substitutes for
    the other.
    """
    from tcg_domain.grade import Grade
    from tcg_grading_companies.errors import UnsupportedGrade
    from tcg_market_data import validated_grade_key

    observe(grading_company="psa", grade="9.5")
    assert query(sa.select(market_observations.c.grade))[0][0] == "9.5"

    with pytest.raises(UnsupportedGrade):
        validated_grade_key("psa", Grade.parse("9.5"))


# ---------------------------------------------------------------------------
# Money — exact, and never floating point
# ---------------------------------------------------------------------------
def test_a_price_is_an_exact_decimal(catalog_and_provider: None) -> None:
    observe(price=Decimal("412.50"))
    stored = query(sa.select(market_observations.c.price))[0][0]
    assert isinstance(stored, Decimal)
    assert stored == Decimal("412.50")


def test_price_arithmetic_shows_no_floating_point_drift(catalog_and_provider: None) -> None:
    """0.1 + 0.2 is the canonical binary-float failure; NUMERIC does not have it."""
    observe(price=Decimal("0.10"))
    observe(price=Decimal("0.20"))
    total = query(sa.select(sa.func.sum(market_observations.c.price)))[0][0]
    assert total == Decimal("0.30")
    assert str(total) == "0.30"


def test_a_zero_price_is_an_observation(catalog_and_provider: None) -> None:
    """A card nobody will pay for really is worth nothing — never an absent price."""
    observe(price=Decimal("0.00"))
    assert query(sa.select(market_observations.c.price))[0][0] == Decimal("0")


def test_a_negative_price_is_refused(catalog_and_provider: None) -> None:
    with pytest.raises(IntegrityError, match="price_is_not_negative"):
        observe(price=Decimal("-1.00"))


@pytest.mark.parametrize("code", ["SGD", "USD", "JPY"])
def test_any_iso_4217_code_is_storable(code: str, catalog_and_provider: None) -> None:
    """The selected provider prices in USD; an observation records what it said."""
    observe(currency=code)
    assert query(sa.select(market_observations.c.currency))[0][0] == code


@pytest.mark.parametrize("code", ["sgd", "SG", "SGDD"])
def test_a_currency_that_is_not_a_code_is_refused(code: str, catalog_and_provider: None) -> None:
    with pytest.raises(IntegrityError, match="currency_is_an_iso_4217_code"):
        observe(currency=code)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_a_confidence_outside_the_unit_interval_is_refused(
    confidence: float, catalog_and_provider: None
) -> None:
    with pytest.raises(IntegrityError, match="confidence_is_a_unit_interval"):
        observe(confidence=confidence)


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------
def test_updating_a_price_is_refused(catalog_and_provider: None) -> None:
    """A corrected price is a new observation — that is what makes history honest."""
    observe()
    with pytest.raises(IntegrityError, match="append-only"):
        write([(sa.text("UPDATE market_observations SET price = 1.00"), None)])


def test_updating_a_providers_licence_is_refused(catalog_and_provider: None) -> None:
    """Otherwise one UPDATE retroactively relicenses every observation already gathered."""
    with pytest.raises(IntegrityError, match="append-only"):
        write([(sa.text("UPDATE market_providers SET commercial_use = false"), None)])


def test_the_refusal_names_the_table(catalog_and_provider: None) -> None:
    """One function serves both tables, so `TG_TABLE_NAME` is what tells them apart."""
    with pytest.raises(IntegrityError, match="market_providers is append-only"):
        write([(sa.text("UPDATE market_providers SET name = 'Other'"), None)])


def test_deleting_an_observation_is_allowed(catalog_and_provider: None) -> None:
    """Deliberate, and the one place this departs from `grading_rules`.

    A daily refresh over the whole catalog is millions of rows a year and will
    eventually need pruning; a trigger refusing DELETE would make that
    impossible.
    """
    observe()
    write([(sa.text("DELETE FROM market_observations"), None)])
    assert query(sa.select(sa.func.count()).select_from(market_observations))[0][0] == 0


def test_truncate_still_empties_the_tables(catalog_and_provider: None) -> None:
    """Bypassing row triggers is what lets the fixtures reset; do not "fix" it."""
    observe()
    write([(sa.text("TRUNCATE market_observations, market_providers CASCADE"), None)])
    assert query(sa.select(sa.func.count()).select_from(market_providers))[0][0] == 0


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def test_a_card_with_prices_cannot_be_removed_from_the_catalog(
    catalog_and_provider: None,
) -> None:
    """RESTRICT, never CASCADE: a re-import must not silently discard price history."""
    observe()
    with pytest.raises(IntegrityError, match="fk_market_observations_card_id_cards"):
        write([(sa.text("DELETE FROM cards"), None)])


def test_a_provider_with_observations_cannot_be_deleted(catalog_and_provider: None) -> None:
    """Which is why no trigger guards DELETE on `market_providers`."""
    observe()
    with pytest.raises(IntegrityError, match="fk_market_observations_provider_id_market_providers"):
        write([(sa.text("DELETE FROM market_providers"), None)])


def test_a_provider_with_no_observations_can_be_deleted(catalog_and_provider: None) -> None:
    """A row registered in error is corrected by removing it, not by editing it."""
    write([(sa.text("DELETE FROM market_providers"), None)])
    assert query(sa.select(sa.func.count()).select_from(market_providers))[0][0] == 0


def test_one_provider_cannot_be_registered_twice(catalog_and_provider: None) -> None:
    """`NULLS NOT DISTINCT` is what makes this hold when no version is published."""
    with pytest.raises(IntegrityError, match="uq_market_providers_slug_version"):
        register(id=uuid.uuid4())


def test_two_versions_of_one_provider_may_coexist(catalog_and_provider: None) -> None:
    """The day a provider publishes a version, a new one is a new row."""
    register(id=uuid.uuid4(), version="2026-08-24")
    assert query(sa.select(sa.func.count()).select_from(market_providers))[0][0] == 2


def test_a_provider_name_that_is_not_a_slug_is_refused() -> None:
    """ADR 0006 binds 'PokePriceTracker'; that goes in `name`, never in `slug`."""
    with pytest.raises(IntegrityError, match="slug_is_a_lowercase_slug"):
        register(slug="PokePriceTracker")


def test_an_observation_needs_a_provider(catalog_and_provider: None) -> None:
    """No orphan prices: every price carries the licence it was gathered under."""
    with pytest.raises(IntegrityError, match="fk_market_observations_provider_id"):
        observe(provider_id=uuid.uuid4())
