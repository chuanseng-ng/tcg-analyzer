"""Shared pytest configuration for the Python workspace.

`Settings` reads a `.env` file so a developer can configure the stack once and
forget about it. That is right for running the service and wrong for running the
suite: from the moment `.env.example` exists, most developers have a `.env`, and
a test asserting a default would then be asserting whatever that developer
happens to have set. The suite would pass here and fail there.

So the file is switched off for the duration of every test. Real environment
variables still apply — integration tests are selected by an exported
`TCG_API_DATABASE_URL` (see CLAUDE.md), and that keeps working; only the
*file* is ignored.

This file also holds the suite's one destructive-action guard (#196): the
integration fixtures truncate whatever `TCG_API_DATABASE_URL` names, and since
#181 one such database holds the training corpus. The guard lives here because
here it covers every truncating fixture at once, including the ones not written
yet.

That leaves one gap, which `unconfigured_environment` below closes. A test
asserting what the service does with a setting **absent** cannot construct
`Settings(_env_file=None)` and stop there: `_env_file` suppresses the file and
nothing else, so on a developer's machine — or in any shell that followed
CLAUDE.md's instruction to export `TCG_API_DATABASE_URL` before running the
integration tests — the setting is present after all and the test asserts the
opposite of what it says. Ask for the fixture instead.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Final

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.config import Settings, get_settings
from tcg_api.rate_limit import get_redis
from tcg_api.storage import get_object_storage

#: The prefix every setting this service reads is spelled with.
_ENV_PREFIX = "TCG_API_"

# Every process-wide cache derived from settings. Clearing settings alone would
# leave a client built from the previous test's environment in place, which is
# the same leak one step further down.
_CACHED_ON_SETTINGS = (get_settings, get_object_storage, get_redis)


#: What the rate limiter is set to for the suite. High enough that no test trips
#: it by accident: `TestClient` reports one address for every request in the
#: process, so a developer who exported `TCG_API_REDIS_URL` alongside
#: `TCG_API_DATABASE_URL` would otherwise have one module's twentieth request
#: throttled and the failure would look like the endpoint's. `test_rate_limit.py`
#: sets its own value, which is where the limit is actually asserted.
_SUITE_RATE_LIMIT = "1000000"


@pytest.fixture(autouse=True)
def _isolate_from_the_developers_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Describe the code, not the machine it happens to be running on."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("TCG_API_RATE_LIMIT_REQUESTS", _SUITE_RATE_LIMIT)
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()
    yield
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()


@pytest.fixture
def unconfigured_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every `TCG_API_` variable for the duration of one test.

    Opt-in rather than autouse, and deliberately so: the suite selects its
    integration and object-storage tests by exported variables, and clearing
    those everywhere would skip the tests that need them. Only a test asserting
    *unconfigured* behaviour wants this.

    The caches are cleared as well as the variables, because a `Settings` object
    built earlier in the session would otherwise still hold the value that was
    just removed.
    """
    for name in [name for name in os.environ if name.startswith(_ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    for cached in _CACHED_ON_SETTINGS:
        cached.cache_clear()


# ---------------------------------------------------------------------------
# #196 — the integration suite must not truncate the training corpus.
# ---------------------------------------------------------------------------

#: What a person cannot get back. Every integration fixture in this repository
#: empties these with `TRUNCATE ... RESTART IDENTITY CASCADE`, which is right for
#: a test database and catastrophic for the one #181 put the corpus in.
#:
#: The photographs re-ingest from disk, so `training_images` and
#: `physical_copies` are recoverable and are here to catch the mistake early.
#: **`image_annotations` and `centering_measurements` are the reason this
#: exists**: hours of a person's judgement, reconstructible from nothing.
#:
#: Object storage is deliberately absent. The `object_storage` tests delete only
#: the keys they created and no test empties a bucket, so the corpus's artifacts
#: were never in the blast radius — the database was.
CORPUS_TABLES: Final = (
    "physical_copies",
    "training_images",
    "image_annotations",
    "centering_measurements",
)


def corpus_guard_message(url: str, counts: Mapping[str, int]) -> str | None:
    """Why this session must stop, or ``None`` if it may proceed.

    Separated from the counting so the decision is testable without a database:
    `tests/test_corpus_guard.py` asserts what the operator is told, which is the
    part that has to be right at the moment somebody is about to lose work.

    The message names the *database*, never the URL — an abort is printed,
    screenshotted and pasted into an issue, and the URL carries a password.
    """
    populated = {name: rows for name, rows in counts.items() if rows}
    if not populated:
        return None

    database = sa.engine.make_url(url).database or "the configured database"
    held = "\n".join(f"  {rows:>7,} {name}" for name, rows in sorted(populated.items()))
    return (
        f"\nTCG_API_DATABASE_URL names the database {database!r}, and it holds "
        f"training-corpus rows:\n\n{held}\n\n"
        "Every integration fixture in this suite starts by running\n"
        "  TRUNCATE ... RESTART IDENTITY CASCADE\n"
        "against that database. Running now would delete all of the above. The "
        "photographs\nre-ingest from disk; the annotations do not: they are "
        "somebody's judgement and\nthere is no copy of them.\n\n"
        "Export the dev/test database instead (the corpus lives in its own), then "
        "run again.\n"
        "If these rows really are debris from a crashed run, TRUNCATE them by hand. "
        "Look\nat what is there first.\n\n"
        "This guard is issue #196. It has fired instead of a mistake."
    )


def _corpus_rows(url: str) -> dict[str, int]:
    """How many rows each corpus table holds, for a database that may have none.

    Every table is optional: `tests/test_migrations.py` leaves the database at
    `base`, so at session start the schema may not exist at all. A database that
    cannot be reached counts as empty — the integration tests are about to fail
    on their own and saying so twice helps nobody.
    """

    async def scenario() -> dict[str, int]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                probe = sa.select(
                    *(sa.func.to_regclass(name).label(name) for name in CORPUS_TABLES)
                )
                found = (await connection.execute(probe)).one()
                existing = [name for name in CORPUS_TABLES if getattr(found, name) is not None]
                if not existing:
                    return {}
                counted = sa.union_all(
                    *(
                        sa.select(
                            sa.literal(name).label("table_name"), sa.func.count().label("rows")
                        ).select_from(sa.table(name))
                        for name in existing
                    )
                )
                return {row.table_name: row.rows for row in await connection.execute(counted)}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(scenario())
    except (SQLAlchemyError, OSError):
        return {}


@pytest.fixture(scope="session", autouse=True)
def _refuse_to_truncate_the_training_corpus() -> None:
    """Stop the whole session before any fixture reaches its first TRUNCATE.

    Session-scoped, so it runs ahead of the module-scoped `migrated` fixtures
    that bring a database up to head, and `pytest.exit` rather than a skip or a
    failure: a skipped module still lets the next one truncate, and the point is
    that nothing gets that far.

    One guard here rather than a check inside each of the nine `empty_tables`
    fixtures — this file is already the whole Python workspace's shared
    configuration, and a fixture added next year is covered without being told.
    """
    url = os.environ.get(f"{_ENV_PREFIX}DATABASE_URL")
    if not url:
        return
    message = corpus_guard_message(url, _corpus_rows(url))
    if message is not None:
        pytest.exit(message, returncode=2)
