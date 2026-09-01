"""Integration tests proving the Alembic harness works end to end.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL, so the
default suite stays hermetic. Start one with:

    docker compose -f infrastructure/local/docker-compose.yml up -d
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to migrate",
    ),
]

HARNESS_TABLE = "migration_harness_check"

# The baseline revision, which is as far as the harness table survives: the
# first domain migration drops it, exactly as its own COMMENT ON TABLE said it
# would. `BASELINE` rather than `head` is therefore what these tests upgrade to,
# and the assertion that `head` has dropped it lives in `test_catalog_schema.py`
# alongside the tables that replaced it.
BASELINE = "0255d9f37125"

# The revision that drops the harness table. Pinned rather than reached with
# `downgrade -1` from `head`, because "-1 from head" means "the newest
# revision", which stopped being this one the moment another landed on top.
# What is under test is that *this* revision reverses.
CATALOG_REVISION = "0d60d1982d83"

# The version record, and the analysis spine that landed on top of it. Pinned
# for the reason above: the two tests below were written against `head`, which
# meant this revision until another one arrived. They are about *these*
# revisions reversing, so they name them.
VERSION_RECORD_REVISION = "352eb3d5e889"
ANALYSIS_REVISION = "29d14fe0fcee"

# Spec §23's grading rules, the second table in this schema to carry an
# immutability trigger. Pinned for the same reason as the two above.
GRADING_RULES_REVISION = "50c399cb7b9b"
# Spec §35's market schema, the third to carry an immutability trigger — and the
# first where one trigger function serves two tables. Pinned for the same reason.
MARKET_DATA_REVISION = "b5bca50f46c0"
# Spec §36's snapshots, and the first revision to reuse an immutability trigger
# function it did not create. Pinned for the same reason as the rest.
MARKET_SNAPSHOTS_REVISION = "3ac71e5d92f8"
ECONOMIC_CONFIGURATION_REVISION = "9d2f61c47ab3"
# Spec §29-§32's dataset domain, the sixth, and the second revision where one
# trigger function serves two tables. Pinned for the same reason as the rest.
DATASETS_REVISION = "6f49252e81d4"
# Spec §28's deduplication, in a revision of its own so the fingerprints can be
# reversed without the corpus going with them. Pinned for the same reason as the
# rest. It creates no trigger function, which is what the second test below is
# about: a `DROP FUNCTION` copied in from the revision underneath would unguard
# `dataset_versions` and `dataset_members`.
FINGERPRINTS_REVISION = "a809e54401d2"
# #165's grading outcome — the label the corpus was missing. Pinned for the same
# reason as the rest. Like the fingerprints it creates no trigger function, and
# it is the second place a `DROP FUNCTION` copied in from a revision underneath
# would unguard `dataset_versions` and `dataset_members`.
GRADING_OUTCOMES_REVISION = "b7e40d2a6c15"

ANALYSIS_TABLES = ("analysis_sessions", "analyses", "images")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"
    return result


def table_exists(name: str) -> bool:
    async def query() -> bool:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                found = await connection.scalar(
                    text("SELECT to_regclass(:qualified) IS NOT NULL"),
                    {"qualified": f"public.{name}"},
                )
            return bool(found)
        finally:
            await engine.dispose()

    return asyncio.run(query())


def function_exists(signature: str) -> bool:
    async def query() -> bool:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                found = await connection.scalar(
                    text("SELECT to_regprocedure(:signature) IS NOT NULL"),
                    {"signature": signature},
                )
            return bool(found)
        finally:
            await engine.dispose()

    return asyncio.run(query())


@pytest.fixture(autouse=True)
def clean_database():
    alembic("downgrade", "base")
    yield
    alembic("downgrade", "base")


def test_upgrade_creates_the_harness_table() -> None:
    alembic("upgrade", BASELINE)

    assert table_exists(HARNESS_TABLE)


def test_downgrade_removes_the_harness_table() -> None:
    alembic("upgrade", BASELINE)

    alembic("downgrade", "base")

    assert not table_exists(HARNESS_TABLE)


def test_the_harness_is_repeatable_on_a_fresh_schema() -> None:
    """The whole history, not just the baseline: this is CI's migrations job."""
    alembic("upgrade", "head")
    alembic("downgrade", "base")

    alembic("upgrade", "head")

    assert table_exists("cards")
    assert table_exists("images")


def test_downgrading_the_catalog_revision_restores_the_harness_table() -> None:
    """`downgrade` is a real inverse, not a declaration.

    The revision that drops the harness table must put it back, or the history
    stops being reversible at exactly the point the first domain table arrives.
    """
    alembic("upgrade", CATALOG_REVISION)

    alembic("downgrade", "-1")

    assert table_exists(HARNESS_TABLE)
    assert not table_exists("cards")


def test_downgrading_the_version_record_leaves_the_catalog_standing() -> None:
    """Each revision reverses only itself.

    The version record arrived on top of the catalog, so undoing it must not
    take the cards with it — that is the difference between a reversible history
    and one that only reverses all the way to `base`.
    """
    alembic("upgrade", VERSION_RECORD_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("card_database_versions")
    assert table_exists("cards")


def test_downgrading_the_version_record_leaves_no_orphaned_trigger_function() -> None:
    """A dropped table takes its trigger; the function it called survives.

    An orphaned `card_database_versions_are_immutable()` would make the next
    `upgrade` silently reuse a definition nobody reviewed, so `downgrade` names
    it explicitly and this proves it.
    """
    alembic("upgrade", VERSION_RECORD_REVISION)

    alembic("downgrade", "-1")

    assert not function_exists("card_database_versions_are_immutable()")


def test_downgrading_the_analysis_revision_leaves_the_catalog_standing() -> None:
    """The analysis spine reverses without touching what it was built on.

    Its only link into the catalog is `analyses.card_id`, and dropping the
    referencing table must not disturb the referenced one.
    """
    alembic("upgrade", ANALYSIS_REVISION)

    alembic("downgrade", "-1")

    for table in ANALYSIS_TABLES:
        assert not table_exists(table), table
    assert table_exists("cards")
    assert table_exists("card_database_versions")


def test_downgrading_the_grading_rules_revision_leaves_the_analysis_spine_standing() -> None:
    """The grading rules landed on top of the analysis spine and reference nothing.

    Nothing has a foreign key into `grading_rules` and it has none out —
    `analyses.grading_rules_version` is a bare identifier by decision — so
    reversing it must disturb nothing at all.
    """
    alembic("upgrade", GRADING_RULES_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("grading_rules")
    for table in ANALYSIS_TABLES:
        assert table_exists(table), table
    assert table_exists("cards")
    assert table_exists("card_database_versions")


def test_downgrading_the_grading_rules_revision_leaves_no_orphaned_trigger_function() -> None:
    """A dropped table takes its trigger; the function it called survives.

    The same trap `card_database_versions` documents, and the reason the second
    trigger-bearing revision names all three drops rather than relying on
    `DROP TABLE`.
    """
    alembic("upgrade", GRADING_RULES_REVISION)
    assert function_exists("grading_rules_are_immutable()")

    alembic("downgrade", "-1")

    assert not function_exists("grading_rules_are_immutable()")


def test_downgrading_the_market_data_revision_leaves_the_rest_standing() -> None:
    """The market tables landed on top of everything and only reference `cards`.

    `market_observations.card_id` points into the catalog, so reversing this must
    leave the catalog untouched rather than cascading into it.
    """
    alembic("upgrade", MARKET_DATA_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("market_observations")
    assert not table_exists("market_providers")
    assert table_exists("grading_rules")
    for table in ANALYSIS_TABLES:
        assert table_exists(table), table
    assert table_exists("cards")
    assert table_exists("card_database_versions")


def test_downgrading_the_market_data_revision_leaves_no_orphaned_trigger_function() -> None:
    """A dropped table takes its trigger; the function it called survives.

    One function serves both market tables here, so the reversal has to drop it
    after the second table rather than alongside either one.
    """
    alembic("upgrade", MARKET_DATA_REVISION)
    assert function_exists("market_rows_are_immutable()")

    alembic("downgrade", "-1")

    assert not function_exists("market_rows_are_immutable()")


def test_downgrading_the_market_snapshots_revision_leaves_the_rest_standing() -> None:
    """Reversing this must not take the prices a snapshot was a cut of.

    It also drops a foreign key on `analyses`, which belongs to another domain
    and another revision — `analyses` itself must survive intact.
    """
    alembic("upgrade", MARKET_SNAPSHOTS_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("market_snapshots")
    assert table_exists("market_observations")
    assert table_exists("market_providers")
    for table in ANALYSIS_TABLES:
        assert table_exists(table), table


def test_downgrading_the_market_snapshots_revision_keeps_the_shared_trigger_function() -> None:
    """The mirror of the orphaned-function test, and the trap it guards is worse.

    This revision reuses `market_rows_are_immutable()` rather than creating it,
    so a copy-pasted `DROP FUNCTION` here would either fail outright or, with
    CASCADE, silently unguard `market_observations` — leaving prices rewritable
    with nothing to say so.
    """
    alembic("upgrade", MARKET_SNAPSHOTS_REVISION)

    alembic("downgrade", "-1")

    assert function_exists("market_rows_are_immutable()")


def test_downgrading_the_economics_revision_leaves_the_rest_standing() -> None:
    """Reversing this drops a foreign key on `analyses`, which is another domain's.

    `analyses` and everything hanging off it must survive intact, exactly as the
    market-snapshot revision's own reversal has to leave the prices standing.
    """
    alembic("upgrade", ECONOMIC_CONFIGURATION_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("economic_configurations")
    for table in ANALYSIS_TABLES:
        assert table_exists(table), table
    assert table_exists("market_snapshots")


def test_downgrading_the_economics_revision_leaves_no_orphaned_trigger_function() -> None:
    """This revision *creates* its own function, so reversing it must drop one.

    The opposite of the market-snapshot revision, which reuses a function two
    other tables still call and must therefore leave it alone. Getting the two
    the wrong way round leaves either an orphan or an unguarded table.
    """
    alembic("upgrade", ECONOMIC_CONFIGURATION_REVISION)

    alembic("downgrade", "-1")

    assert not function_exists("economic_configuration_is_immutable()")


def test_downgrading_the_datasets_revision_leaves_the_rest_standing() -> None:
    """The sixth domain reverses without taking the other five with it."""
    alembic("upgrade", DATASETS_REVISION)

    alembic("downgrade", "-1")

    for table in ("physical_copies", "training_images", "dataset_versions", "dataset_members"):
        assert not table_exists(table), table
    assert table_exists("economic_configurations")
    assert table_exists("market_snapshots")
    assert table_exists("cards")


def test_downgrading_the_datasets_revision_leaves_no_orphaned_trigger_function() -> None:
    """This revision creates its own shared function, so reversing it must drop one.

    `DROP TABLE` takes each trigger with it but never the function the two share,
    which is why the drop is named explicitly and runs last.
    """
    alembic("upgrade", DATASETS_REVISION)

    alembic("downgrade", "-1")

    assert not function_exists("dataset_records_are_immutable()")
    assert function_exists("market_rows_are_immutable()")


def test_downgrading_the_fingerprints_revision_leaves_the_corpus_standing() -> None:
    """The fingerprints go and the images they describe stay.

    A fingerprint is derived data: reversing #155 must cost the corpus nothing.
    """
    alembic("upgrade", FINGERPRINTS_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("training_image_fingerprints")
    for table in ("physical_copies", "training_images", "dataset_versions", "dataset_members"):
        assert table_exists(table), table


def test_downgrading_the_fingerprints_revision_keeps_the_shared_trigger_function() -> None:
    """This revision creates no function, so reversing it must drop none.

    The trap is the revision underneath, which does create one and names the drop
    explicitly. Copying that `DROP FUNCTION` into this `downgrade()` would leave
    `dataset_versions` and `dataset_members` still standing and no longer
    immutable — a silent loss of spec §31's guarantee.
    """
    alembic("upgrade", FINGERPRINTS_REVISION)

    alembic("downgrade", "-1")

    assert function_exists("dataset_records_are_immutable()")
    assert function_exists("market_rows_are_immutable()")


def test_downgrading_the_grading_outcomes_revision_leaves_the_corpus_standing() -> None:
    """The label goes and the corpus it labels stays.

    Reversing #165 must cost neither a photograph nor a published version — it
    adds one table and nothing else.
    """
    alembic("upgrade", GRADING_OUTCOMES_REVISION)

    alembic("downgrade", "-1")

    assert not table_exists("grading_outcomes")
    for table in ("physical_copies", "training_images", "dataset_versions", "dataset_members"):
        assert table_exists(table), table
    assert table_exists("model_bundles")


def test_downgrading_the_grading_outcomes_revision_keeps_the_shared_trigger_function() -> None:
    """This revision creates no function, so reversing it must drop none.

    The fingerprints revision documents the same trap: a `DROP FUNCTION
    dataset_records_are_immutable()` copied in from the dataset revision would
    leave `dataset_versions` and `dataset_members` standing and no longer
    immutable — a silent loss of spec §31's guarantee.
    """
    alembic("upgrade", GRADING_OUTCOMES_REVISION)

    alembic("downgrade", "-1")

    assert function_exists("dataset_records_are_immutable()")
    assert function_exists("market_rows_are_immutable()")


def test_the_harness_table_documents_why_it_exists() -> None:
    """The baseline table must announce that it is scaffolding, not domain."""
    alembic("upgrade", BASELINE)

    async def read_comment() -> str | None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await connection.scalar(
                    text("SELECT obj_description(to_regclass(:qualified), 'pg_class')"),
                    {"qualified": f"public.{HARNESS_TABLE}"},
                )
        finally:
            await engine.dispose()

    comment = asyncio.run(read_comment())
    assert comment is not None
    assert "harness" in comment.lower()


def test_alembic_fails_clearly_when_the_database_url_is_unset() -> None:
    environment = {k: v for k, v in os.environ.items() if k != "TCG_API_DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert "TCG_API_DATABASE_URL" in result.stderr
