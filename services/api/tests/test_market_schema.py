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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tcg_api.analysis.sessions import record_reproducibility
from tcg_api.analysis.tables import analyses, analysis_sessions
from tcg_api.catalog.tables import cards, sets
from tcg_api.market.snapshots import (
    current_snapshot,
    generate_snapshot,
    get_snapshot,
    resolve_prices,
)
from tcg_api.market.tables import market_observations, market_providers, market_snapshots
from tcg_domain.catalog import Card, Set
from tcg_grading_companies import GradingCompany
from tcg_grading_companies.errors import UnsupportedGrade
from tcg_market_data import MarketSnapshot, PriceObservation

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
                    "TRUNCATE market_snapshots, market_observations, market_providers, "
                    "images, analyses, analysis_sessions, "
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
SESSION_ID = uuid.UUID("44444444-4444-5444-8444-444444444444")
ANALYSIS_ID = uuid.UUID("55555555-5555-5555-8555-555555555555")

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


def open_an_analysis() -> None:
    """A session and one analysis, so `market_snapshot_id` has a row to sit on."""
    write(
        [
            (
                sa.insert(analysis_sessions),
                {
                    "id": SESSION_ID,
                    "anonymous_session_id": "wCq3nB0Xr4h8kJ2vL7pT1yZ6sD9aF5gE",
                    # Off the real clock, for the reason
                    # `test_analysis_schema.py`'s `session_values` is: this one
                    # was five days from the same failure.
                    "expires_at": datetime.now(UTC) + timedelta(days=7),
                    "application_version": "0.1.0",
                },
            ),
            (sa.insert(analyses), {"id": ANALYSIS_ID, "session_id": SESSION_ID}),
        ]
    )


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


# ---------------------------------------------------------------------------
# Spec §36 — a snapshot is a cut-line, and it never moves
# ---------------------------------------------------------------------------
# The card is rebuilt here as a domain entity rather than read back through the
# repository: `resolve_prices` needs a `CardReference`, and a schema test that
# went through the catalog adapter to get one would fail for the adapter's
# reasons as well as its own.
CARD = Card(
    id=CARD_ID,
    set=Set(
        id=SET_ID,
        game="pokemon",
        language="en",
        set_code="BS",
        name="Base Set",
    ),
    card_number="4/102",
    name="Charizard",
    variant="unlimited-holo",
)


def run_async(work: Callable[[AsyncSession], Any]) -> Any:
    """Run one coroutine factory against a fresh session, committing what it wrote."""

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


def cut() -> MarketSnapshot:
    """Generate a snapshot of the fixture provider's prices."""
    return run_async(lambda session: generate_snapshot(session, provider_id=PROVIDER_ID))


def cut_at(generated_at: datetime) -> MarketSnapshot:
    """A snapshot with a chosen cut, written directly.

    `generate_snapshot` deliberately takes no `generated_at` — a caller that
    could choose one could backdate a cut past prices that had already landed.
    A test that needs to place a cut relative to a row's `created_at` therefore
    writes the row itself; it is exercising the resolution rule, not the
    generator.
    """
    snapshot_id = uuid.uuid4()
    write(
        [
            (
                sa.insert(market_snapshots),
                {"id": snapshot_id, "provider_id": PROVIDER_ID, "generated_at": generated_at},
            )
        ]
    )
    resolved = run_async(lambda session: get_snapshot(session, snapshot_id))
    assert resolved is not None
    return resolved


def prices(snapshot: MarketSnapshot) -> tuple[PriceObservation, ...]:
    return run_async(lambda session: resolve_prices(session, snapshot, CARD))


def keys(observations: tuple[PriceObservation, ...]) -> list[tuple[str | None, str | None, str]]:
    """What a caller actually reads: the key, and the price under it."""
    return [
        (
            observation.grading_company,
            None if observation.grade is None else str(observation.grade),
            str(observation.price.amount),
        )
        for observation in observations
    ]


def test_a_snapshot_records_its_provider_and_its_cut(catalog_and_provider: None) -> None:
    snapshot = cut()

    assert snapshot.provider == PROVIDER_ID
    assert snapshot.generated_at.utcoffset() is not None
    assert snapshot.data_version == snapshot.generated_at.astimezone(UTC).date()


def test_the_data_version_cannot_be_written(catalog_and_provider: None) -> None:
    """Generated, exactly as `market_type` is — and refused the same way."""
    with pytest.raises(DBAPIError, match="data_version"):
        write(
            [
                (
                    sa.insert(market_snapshots),
                    {
                        "id": uuid.uuid4(),
                        "provider_id": PROVIDER_ID,
                        "data_version": date(2020, 1, 1),
                    },
                )
            ]
        )


def test_the_data_version_is_the_ingestion_date_in_utc(catalog_and_provider: None) -> None:
    """A cut at 23:30 UTC stamps that day, whatever the server's timezone is."""
    snapshot_id = uuid.uuid4()
    write(
        [
            (
                sa.insert(market_snapshots),
                {
                    "id": snapshot_id,
                    "provider_id": PROVIDER_ID,
                    "generated_at": datetime(2026, 8, 24, 23, 30, tzinfo=UTC),
                },
            )
        ]
    )

    stored = query(
        sa.select(market_snapshots.c.data_version).where(market_snapshots.c.id == snapshot_id)
    )

    assert stored[0][0] == date(2026, 8, 24)


def test_a_snapshot_resolves_the_latest_price_per_key(catalog_and_provider: None) -> None:
    """One entry per (company, grade), raw included, newest first."""
    observe(price=Decimal("400.00"), observed_at=SEEN_AT - timedelta(days=1))
    observe(price=Decimal("412.50"))
    observe(grading_company="psa", grade="10", price=Decimal("9000.00"))

    assert keys(prices(cut())) == [
        (None, None, "412.50"),
        ("psa", "10", "9000.00"),
    ]


def test_a_generated_snapshot_does_not_change_when_new_prices_arrive(
    catalog_and_provider: None,
) -> None:
    """The acceptance criterion: re-resolving a snapshot returns identical prices."""
    observe(price=Decimal("412.50"))
    snapshot = cut()
    before = keys(prices(snapshot))

    observe(price=Decimal("999.99"), observed_at=SEEN_AT + timedelta(days=1))
    observe(grading_company="bgs", grade="9.5", price=Decimal("5000.00"))

    assert keys(prices(snapshot)) == before == [(None, None, "412.50")]


def test_a_backfilled_price_cannot_join_a_snapshot_already_cut(
    catalog_and_provider: None,
) -> None:
    """The one test that proves the cut is on `created_at` and not `observed_at`.

    A backfilled observation is *seen* long before it is stored. Cut on when it
    was seen, it would join a snapshot generated before it arrived — and an
    immutable snapshot that resolves differently on two readings of the same
    data is not immutable at all.
    """
    observe(price=Decimal("412.50"))
    snapshot = cut()

    # Newer than everything by `observed_at`, so it would win outright if it were
    # in the set — and stored a day after the cut, so it is not.
    observe(
        price=Decimal("1.00"),
        observed_at=SEEN_AT + timedelta(hours=1),
        created_at=snapshot.generated_at + timedelta(days=1),
    )

    assert keys(prices(snapshot)) == [(None, None, "412.50")]
    assert keys(prices(cut_at(snapshot.generated_at + timedelta(days=2)))) == [(None, None, "1.00")]


def test_a_correction_supersedes_what_it_corrects(catalog_and_provider: None) -> None:
    """Same `observed_at`, later row. "A corrected price is a new observation.""" ""
    observe(price=Decimal("412.50"), created_at=SEEN_AT)
    observe(price=Decimal("450.00"), created_at=SEEN_AT + timedelta(minutes=1))

    assert keys(prices(cut())) == [(None, None, "450.00")]


def test_raw_prices_collapse_into_one_entry(catalog_and_provider: None) -> None:
    """`grading_company IS NULL` is one group, not one group per row."""
    for day in range(5):
        observe(price=Decimal(f"{400 + day}.00"), observed_at=SEEN_AT - timedelta(days=day))

    resolved = prices(cut())

    assert len(resolved) == 1
    assert resolved[0].grading_company is None


def test_each_graded_key_is_its_own_entry(catalog_and_provider: None) -> None:
    """§35 keys a graded price by (company, grade), and so does a snapshot."""
    observe(grading_company="psa", grade="9", price=Decimal("1000.00"))
    observe(grading_company="psa", grade="10", price=Decimal("9000.00"))
    observe(grading_company="bgs", grade="9.5", price=Decimal("5000.00"))

    # `grade` is a text key under `COLLATE "C"`, so '10' sorts before '9'. The
    # order is stable and total, which is all a snapshot promises — a caller
    # keys by (company, grade) and never reads this sequence as a scale.
    assert keys(prices(cut())) == [
        ("bgs", "9.5", "5000.00"),
        ("psa", "10", "9000.00"),
        ("psa", "9", "1000.00"),
    ]


def test_only_the_snapshots_own_providers_prices_are_in_it(catalog_and_provider: None) -> None:
    """Two providers' figures for one card are two answers, not one blended one."""
    other = uuid.UUID("77777777-7777-5777-8777-777777777777")
    register(id=other, slug="othersource")
    observe(price=Decimal("412.50"))
    observe(provider_id=other, price=Decimal("1.00"))

    assert keys(prices(cut())) == [(None, None, "412.50")]


def test_a_card_nobody_has_priced_resolves_to_nothing(catalog_and_provider: None) -> None:
    """An empty answer, not an error — #55 owns how that reaches a user."""
    assert prices(cut()) == ()


def test_a_stored_grade_is_checked_against_the_company_on_the_way_out(
    catalog_and_provider: None,
) -> None:
    """`grade`'s CHECK is the grammar; the company's scale is enforced in Python.

    `market_observations` will store a PSA 9.5 — deliberately, so a fourth
    company costs no migration — and constructing the `PriceObservation` is
    where that becomes the error it is.
    """
    observe(grading_company="psa", grade="9.5", price=Decimal("1.00"))

    with pytest.raises(UnsupportedGrade, match=r"psa does not issue grade 9\.5"):
        prices(cut())


# ---------------------------------------------------------------------------
# Resolving a snapshot by identifier, which is what an analysis does
# ---------------------------------------------------------------------------
def test_current_snapshot_is_the_most_recently_generated(catalog_and_provider: None) -> None:
    cut()
    second = cut()

    assert run_async(current_snapshot) == second


def test_nothing_generated_has_no_current_snapshot(catalog_and_provider: None) -> None:
    """`None` is the V1 answer, and it is a fact rather than a gap."""
    assert run_async(current_snapshot) is None


def test_an_unknown_identifier_resolves_to_nothing(catalog_and_provider: None) -> None:
    assert run_async(lambda session: get_snapshot(session, uuid.uuid4())) is None


def test_an_analysis_records_and_re_resolves_its_snapshot(catalog_and_provider: None) -> None:
    """Spec §57 end to end: the identifier is written, read back, and re-resolved.

    Two ingestion runs happen between the recording and the re-reading, which is
    the point — the analysis resolves the prices it was computed against rather
    than the ones current when somebody asks.
    """
    observe(price=Decimal("412.50"))
    snapshot = cut()
    open_an_analysis()

    run_async(
        lambda session: record_reproducibility(
            session,
            ANALYSIS_ID,
            application_version="0.1.0",
            card_database_version=None,
            market_snapshot_id=snapshot.id,
        )
    )

    observe(price=Decimal("999.99"), observed_at=SEEN_AT + timedelta(days=1))
    cut()

    recorded = query(sa.select(analyses.c.market_snapshot_id).where(analyses.c.id == ANALYSIS_ID))[
        0
    ][0]
    resolved = run_async(lambda session: get_snapshot(session, recorded))

    assert recorded == snapshot.id
    assert resolved == snapshot
    assert keys(prices(resolved)) == [(None, None, "412.50")]


def test_an_analysis_cannot_name_a_snapshot_that_does_not_exist(
    catalog_and_provider: None,
) -> None:
    """The key `analyses.market_snapshot_id` waited for this milestone to get."""
    open_an_analysis()

    with pytest.raises(IntegrityError, match="fk_analyses_market_snapshot_id_market_snapshots"):
        write(
            [
                (
                    sa.update(analyses).where(analyses.c.id == ANALYSIS_ID),
                    {"market_snapshot_id": uuid.uuid4()},
                )
            ]
        )


def test_a_snapshot_an_analysis_used_cannot_be_deleted(catalog_and_provider: None) -> None:
    """RESTRICT: pruning must never make a recorded analysis unresolvable."""
    snapshot = cut()
    open_an_analysis()
    write(
        [
            (
                sa.update(analyses).where(analyses.c.id == ANALYSIS_ID),
                {"market_snapshot_id": snapshot.id},
            )
        ]
    )

    with pytest.raises(IntegrityError, match="fk_analyses_market_snapshot_id_market_snapshots"):
        write([(sa.text("DELETE FROM market_snapshots"), None)])


# ---------------------------------------------------------------------------
# Append-only, for the third table
# ---------------------------------------------------------------------------
def test_rewriting_a_snapshot_is_refused(catalog_and_provider: None) -> None:
    """A snapshot that could be moved would take every analysis naming it with it."""
    cut()

    with pytest.raises(IntegrityError, match="market_snapshots is append-only"):
        write([(sa.text("UPDATE market_snapshots SET provider_id = provider_id"), None)])


def test_deleting_a_snapshot_nothing_references_is_allowed(catalog_and_provider: None) -> None:
    """DELETE stays open, as it does for observations: pruning is a policy, not a bug."""
    cut()
    write([(sa.text("DELETE FROM market_snapshots"), None)])

    assert query(sa.select(sa.func.count()).select_from(market_snapshots))[0][0] == 0


def test_a_provider_with_snapshots_cannot_be_deleted(catalog_and_provider: None) -> None:
    cut()

    with pytest.raises(IntegrityError, match="fk_market_snapshots_provider_id"):
        write([(sa.text("DELETE FROM market_providers"), None)])
