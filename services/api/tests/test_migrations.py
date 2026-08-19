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
