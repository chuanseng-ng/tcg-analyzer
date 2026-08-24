"""Integration tests for `grading_rules` as PostgreSQL actually built it.

`test_grading_tables.py` proves the schema was *declared* correctly. This proves
the migration built what was declared, and the three guarantees only a real
database can demonstrate: a published version cannot be rewritten or deleted,
one company's effective ranges cannot overlap, and the version in force on a
given date resolves correctly — including on the two boundaries, which is where
a half-open range is either right or quietly off by a day.

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
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tcg_api.grading.rules import get_rules, rules_in_force
from tcg_api.grading.seed import apply_grading_rules, load_grading_rules
from tcg_api.grading.tables import grading_rules
from tcg_grading_companies import GradingRules

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to inspect",
    ),
]

SOURCE = "https://example.test/standards"
READ_ON = date(2026, 8, 24)

# Two PSA versions and one undated TAG version. The PSA pair is what makes a
# boundary a boundary: without a successor there is nothing for the range to end
# at, and every date would resolve to the only row there is.
PSA_OLD = "psa-rules-2008"
PSA_NEW = "psa-rules-2026-08-24"
TAG_UNDATED = "tag-rules-2026-08-24"
PSA_NEW_FROM = date(2026, 8, 24)


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


def run_async(work: Callable[[AsyncSession], Any]) -> Any:
    """Run one coroutine factory against a fresh session — for the resolver."""

    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with AsyncSession(engine) as session:
                return await work(session)
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


def publish(
    version: str,
    company: str = "psa",
    effective_from: date | None = None,
    source: str = SOURCE,
) -> None:
    write(
        [
            (
                sa.insert(grading_rules),
                {
                    "version": version,
                    "company": company,
                    "effective_from": effective_from,
                    "source": source,
                    "verified_on": READ_ON,
                },
            )
        ]
    )


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Every test in this module reads the schema at `head`.

    `test_migrations.py` deliberately leaves the database at `base`, and pytest
    makes no promise about which module runs first.
    """
    alembic("upgrade", "head")


@pytest.fixture(autouse=True)
def empty_table() -> Iterator[None]:
    def truncate(connection: Connection) -> None:
        with connection.begin():
            # TRUNCATE bypasses row-level triggers, which is the only reason
            # this table can be emptied at all: its trigger refuses DELETE.
            connection.execute(sa.text("TRUNCATE grading_rules RESTART IDENTITY CASCADE"))

    run_sync(truncate)
    yield
    run_sync(truncate)


@pytest.fixture
def three_versions() -> None:
    """Two PSA standards in sequence, and one TAG standard with no stated start."""
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    publish(PSA_NEW, effective_from=PSA_NEW_FROM)
    publish(TAG_UNDATED, company="tag", effective_from=None)


def in_force(company: str, on: date) -> GradingRules | None:
    return run_async(lambda session: rules_in_force(session, company, on))


# ---------------------------------------------------------------------------
# "Updating a published version is rejected" — spec §23, the issue's second test
# ---------------------------------------------------------------------------
def test_updating_a_published_version_is_refused() -> None:
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    with pytest.raises(IntegrityError, match="immutable"):
        write([(sa.text("UPDATE grading_rules SET source = 'https://elsewhere.test'"), None)])


def test_updating_a_published_version_to_its_own_values_is_still_refused() -> None:
    """The flat trigger has no `IS DISTINCT FROM` escape, and that is on purpose.

    `trg_analyses_reproducibility_immutable` has one because a replayed write of
    the same values must be a no-op rather than a failure there. Here a second
    write of any kind is a caller that has misunderstood what a published version
    is, and it should hear about it.
    """
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    with pytest.raises(IntegrityError, match="immutable"):
        write([(sa.update(grading_rules).values(source=SOURCE), None)])


def test_deleting_a_published_version_is_refused() -> None:
    """An analysis that recorded a version must resolve it however old it is."""
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    with pytest.raises(IntegrityError, match="immutable"):
        write([(sa.text("DELETE FROM grading_rules"), None)])


def test_truncate_still_empties_the_table() -> None:
    """Bypassing row triggers is what lets the fixtures reset; do not "fix" it.

    The trigger guards the mutation path application code can reach. A
    statement-level trigger would guard TRUNCATE too and leave every integration
    test in this file unable to start from an empty table.
    """
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    write([(sa.text("TRUNCATE grading_rules"), None)])
    assert query(sa.select(sa.func.count()).select_from(grading_rules))[0][0] == 0


# ---------------------------------------------------------------------------
# "Overlapping ranges for one company are rejected" — the issue's fourth test
# ---------------------------------------------------------------------------
def test_two_versions_of_one_company_cannot_share_an_effective_date() -> None:
    """Given derived ranges this is the *only* way one company can overlap itself."""
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    with pytest.raises(IntegrityError, match="uq_grading_rules_company_effective_from"):
        publish("psa-rules-duplicate", effective_from=date(2008, 2, 1))


def test_two_undated_versions_of_one_company_are_refused() -> None:
    """The `NULLS NOT DISTINCT` case, which a default UNIQUE would accept.

    TAG and BGS publish no effective date at all, so this is not a hypothetical
    shape: two undated standards for one company would leave "which was in
    force" with no answer at all.
    """
    publish(TAG_UNDATED, company="tag", effective_from=None)
    with pytest.raises(IntegrityError, match="uq_grading_rules_company_effective_from"):
        publish("tag-rules-duplicate", company="tag", effective_from=None)


def test_two_companies_may_share_an_effective_date() -> None:
    """Without this the two tests above would pass for the wrong reason."""
    publish(PSA_OLD, effective_from=date(2008, 2, 1))
    publish("tag-rules-2008", company="tag", effective_from=date(2008, 2, 1))
    assert query(sa.select(sa.func.count()).select_from(grading_rules))[0][0] == 2


# ---------------------------------------------------------------------------
# "Rules resolve correctly for a given date, including at boundaries"
# ---------------------------------------------------------------------------
def test_a_version_is_in_force_on_its_own_effective_date(three_versions: None) -> None:
    """The inclusive lower bound.

    PSA's own wording — "Starting February 1, 2008, all cards submitted to PSA
    will be graded utilizing this new scale" — makes that date the new scale's
    first day.
    """
    resolved = in_force("psa", date(2008, 2, 1))
    assert resolved is not None
    assert resolved.version == PSA_OLD


def test_the_day_before_a_successor_still_resolves_to_the_predecessor(
    three_versions: None,
) -> None:
    resolved = in_force("psa", date(2026, 8, 23))
    assert resolved is not None
    assert resolved.version == PSA_OLD


def test_a_successors_effective_date_resolves_to_the_successor(three_versions: None) -> None:
    """The exclusive upper bound — the half-open range's other end."""
    resolved = in_force("psa", PSA_NEW_FROM)
    assert resolved is not None
    assert resolved.version == PSA_NEW


def test_the_resolved_record_carries_the_derived_effective_to(three_versions: None) -> None:
    """§23's column, computed rather than stored, and indistinguishable from it."""
    superseded = in_force("psa", date(2010, 1, 1))
    assert superseded is not None
    assert superseded.effective_from == date(2008, 2, 1)
    assert superseded.effective_to == PSA_NEW_FROM

    current = in_force("psa", date(2026, 12, 31))
    assert current is not None
    assert current.effective_to is None


def test_a_company_that_states_no_effective_date_is_in_force_at_any_date(
    three_versions: None,
) -> None:
    """And `effective_from` comes back absent, never as a fabricated date."""
    for on in (date(1999, 1, 1), date(2026, 8, 24)):
        resolved = in_force("tag", on)
        assert resolved is not None, on
        assert resolved.version == TAG_UNDATED
        assert resolved.effective_from is None


def test_nothing_is_in_force_before_the_earliest_effective_date(three_versions: None) -> None:
    """Never a fallback to the oldest version, which did not exist yet."""
    assert in_force("psa", date(2000, 1, 1)) is None


def test_an_unknown_company_resolves_to_nothing(three_versions: None) -> None:
    assert in_force("cgc", date(2026, 8, 24)) is None


def test_a_historical_analysis_resolves_the_exact_version_it_recorded(
    three_versions: None,
) -> None:
    """The acceptance criterion, with a newer version already published.

    An analysis holds the identifier in `analyses.grading_rules_version`; what it
    must get back is the standard it actually ran under, not whatever is current
    now.
    """
    recorded = run_async(lambda session: get_rules(session, PSA_OLD))
    assert recorded is not None
    assert recorded.version == PSA_OLD
    assert recorded.effective_from == date(2008, 2, 1)
    assert recorded.effective_to == PSA_NEW_FROM
    assert run_async(lambda session: get_rules(session, "psa-rules-never-published")) is None


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------
def test_seeding_writes_one_row_per_adapter_and_is_idempotent() -> None:
    """Re-running converges rather than rewriting — `register_version`'s policy.

    Running it a second time is not a tidiness check: the trigger would refuse an
    `ON CONFLICT DO UPDATE`, so an `IntegrityError` here is what a policy drift
    would look like.
    """
    records = load_grading_rules()

    async def apply() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await apply_grading_rules(records, engine)
            await apply_grading_rules(records, engine)
        finally:
            await engine.dispose()

    asyncio.run(apply())

    rows = query(
        sa.select(
            grading_rules.c.company,
            grading_rules.c.version,
            grading_rules.c.effective_from,
            grading_rules.c.source,
            grading_rules.c.rules,
        ).order_by(grading_rules.c.company)
    )
    assert [row.company for row in rows] == ["bgs", "psa", "tag"]
    assert {row.effective_from for row in rows} == {None, date(2008, 2, 1)}
    assert all(row.source.startswith("https://") for row in rows)
    assert all(row.rules == {} for row in rows)


def test_the_seeded_current_version_resolves_for_every_company() -> None:
    records = load_grading_rules()

    async def apply() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await apply_grading_rules(records, engine)
        finally:
            await engine.dispose()

    asyncio.run(apply())

    for record in records:
        resolved = in_force(record.company, READ_ON)
        assert resolved is not None, record.company
        assert resolved.version == record.version
        assert resolved.effective_to is None
