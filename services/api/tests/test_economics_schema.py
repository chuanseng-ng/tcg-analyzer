"""`economic_configurations` against a real PostgreSQL — #65.

`test_economics_tables.py` asserts what was *declared*; this asserts what the
database actually does after the migration has run. The two are not the same
claim, and the gap between them is where a CHECK that exists in `tables.py` and
not in the migration would live — Alembic compares a check's name but never its
text, so nothing else would notice.

What is here is what nothing else covers. The endpoint validates a configuration
before it inserts one, so every constraint below is the **second** guard: it is
what stops a bug in the boundary, a future writer, or a hand-run `psql` from
storing a negative fee or a rate of ten. A guard nobody tests is a guard nobody
knows is gone.

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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.tables import analyses, analysis_sessions
from tcg_api.economics.tables import economic_configurations

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

#: A row every constraint accepts. Each test below spoils exactly one field, so
#: a failure names the rule that was broken rather than "something was wrong".
LEGAL: dict[str, Any] = {
    "currency": "SGD",
    "acquisition_cost": None,
    "grading_fee": Decimal("40.00"),
    "outbound_shipping": Decimal("30.00"),
    "return_shipping": Decimal("30.00"),
    "insurance": Decimal("0.00"),
    "miscellaneous": Decimal("0.00"),
    "selling_fee_rate": Decimal("0.1000"),
    "selling_fee_flat": Decimal("0.00"),
    "grading_companies": ["psa", "bgs"],
    "optimization_mode": "expected_profit",
    "minimum_image_quality": 0.5,
    "minimum_grade_confidence": 0.5,
    "minimum_figure_confidence": 0.4,
    "maximum_unpriced_probability": 0.25,
    "minimum_incremental_profit": Decimal("5.00"),
}


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base` and pytest orders nothing."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def empty_tables() -> Any:
    def truncate() -> None:
        async def scenario() -> None:
            engine = create_async_engine(DATABASE_URL or "")
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        sa.text(
                            "TRUNCATE analysis_sessions, economic_configurations "
                            "RESTART IDENTITY CASCADE"
                        )
                    )
            finally:
                await engine.dispose()

        run(scenario)

    truncate()
    yield
    truncate()


def write(statement: Any, values: Any) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement, values)
        finally:
            await engine.dispose()

    run(scenario)


def insert_configuration(**overrides: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    write(sa.insert(economic_configurations), {"id": identifier, **LEGAL, **overrides})
    return identifier


def insert_analysis(configuration_id: uuid.UUID | None) -> uuid.UUID:
    session_id = uuid.uuid4()
    analysis_id = uuid.uuid4()
    write(
        sa.insert(analysis_sessions),
        {
            "id": session_id,
            "anonymous_session_id": uuid.uuid4().hex,
            "application_version": "0.1.0",
            # `expires_after_it_was_created` refuses a row inserted already
            # expired, so this is comfortably in the future.
            "expires_at": datetime.now(UTC) + timedelta(days=7),
        },
    )
    write(
        sa.insert(analyses),
        {
            "id": analysis_id,
            "session_id": session_id,
            "economic_configuration_id": configuration_id,
        },
    )
    return analysis_id


def delete_configuration(configuration_id: uuid.UUID) -> None:
    write(
        sa.delete(economic_configurations).where(economic_configurations.c.id == configuration_id),
        {},
    )


# ---------------------------------------------------------------------------
# The row the constraints accept
# ---------------------------------------------------------------------------


def test_a_legal_configuration_is_stored() -> None:
    """The control. Without it every refusal below could be refusing everything."""
    assert insert_configuration()


def test_a_zero_acquisition_cost_is_legal_and_null_is_a_different_row() -> None:
    """§45: absent is not zero, and the column has to be able to hold both."""
    absent = insert_configuration(acquisition_cost=None)
    free = insert_configuration(acquisition_cost=Decimal("0.00"))

    assert absent != free


# ---------------------------------------------------------------------------
# What the constraints refuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grading_fee", Decimal("-0.01")),
        ("outbound_shipping", Decimal("-1.00")),
        ("return_shipping", Decimal("-1.00")),
        ("insurance", Decimal("-1.00")),
        ("miscellaneous", Decimal("-1.00")),
        ("selling_fee_flat", Decimal("-1.00")),
        ("acquisition_cost", Decimal("-1.00")),
        ("minimum_incremental_profit", Decimal("-1.00")),
    ],
)
def test_a_negative_amount_is_refused(field: str, value: Decimal) -> None:
    """ADR 0007 asserts neither `CapitalAtRisk` denominator can be negative.

    That claim holds only while every amount it is summed from is non-negative,
    so the database says so as well as the engine.
    """
    with pytest.raises(IntegrityError, match="is_not_negative"):
        insert_configuration(**{field: value})


def test_a_percentage_shaped_selling_fee_rate_is_refused() -> None:
    """`Decimal("10")` is 1000%, and would charge a fee a hundred times too large."""
    with pytest.raises(IntegrityError, match="selling_fee_rate_is_a_proportion"):
        insert_configuration(selling_fee_rate=Decimal("10"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_image_quality", 1.5),
        ("minimum_grade_confidence", -0.1),
        ("minimum_figure_confidence", 2.0),
        ("maximum_unpriced_probability", 1.01),
    ],
)
def test_a_threshold_outside_the_unit_interval_is_refused(field: str, value: float) -> None:
    """`Confidence` refuses these in memory; this is what refuses them on disk."""
    with pytest.raises(IntegrityError, match="in_unit_range"):
        insert_configuration(**{field: value})


def test_an_empty_company_selection_is_refused() -> None:
    """Nothing to compare makes every §44 answer `no_company_can_be_ranked`.

    `cardinality`, not `array_length`: the latter is NULL for an empty array and
    a NULL CHECK *passes*, so the obvious spelling would refuse nothing at all.
    """
    with pytest.raises(IntegrityError, match="at_least_one_grading_company"):
        insert_configuration(grading_companies=[])


def test_an_unmodelled_currency_is_refused() -> None:
    """Unlike `market_observations.currency`, which records what a provider said."""
    with pytest.raises(IntegrityError, match="currency_is_a_known_currency"):
        insert_configuration(currency="USD")


def test_an_unregistered_optimization_mode_is_accepted_by_the_database() -> None:
    """Deliberate: §43 requires future modes, so the CHECK is at the boundary.

    A sixth mode must cost one strategy object and no migration, which is #63's
    binding. `POST /analyses/{id}/economic-configuration` is what refuses one
    nothing implements.
    """
    assert insert_configuration(optimization_mode="lowest_regret")


def test_an_unregistered_grading_company_is_accepted_by_the_database() -> None:
    """Same argument: `grading_rules.company` carries no CHECK either."""
    assert insert_configuration(grading_companies=["cgc"])


# ---------------------------------------------------------------------------
# Write-once, and deletable
# ---------------------------------------------------------------------------


def test_a_stored_configuration_cannot_be_updated() -> None:
    """The numbers a past recommendation was computed from do not move — §57."""
    identifier = insert_configuration()

    with pytest.raises(IntegrityError, match="immutable"):
        write(
            sa.update(economic_configurations)
            .where(economic_configurations.c.id == identifier)
            .values(grading_fee=Decimal("1.00")),
            {},
        )


def test_a_stored_configuration_can_be_deleted() -> None:
    """The trigger guards `UPDATE` only, so spec §54's sweep can still remove one."""
    identifier = insert_configuration()

    delete_configuration(identifier)


def test_a_configuration_an_analysis_names_cannot_be_deleted() -> None:
    """RESTRICT: a configuration *is* the numbers an analysis was computed under.

    Which is also why the retention sweep deletes the analysis first and the
    configuration afterwards.
    """
    identifier = insert_configuration()
    insert_analysis(identifier)

    with pytest.raises(IntegrityError, match="fk_analyses_economic_configuration_id"):
        delete_configuration(identifier)


def test_an_analysis_cannot_name_a_configuration_that_was_never_written() -> None:
    """The key is what makes §57's record resolvable rather than merely stored."""
    with pytest.raises(IntegrityError, match="fk_analyses_economic_configuration_id"):
        insert_analysis(uuid.uuid4())
