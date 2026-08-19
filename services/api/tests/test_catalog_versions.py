"""The PostgreSQL side of `CardDatabaseVersionRepository` — issue #27.

`test_catalog_schema.py` proves the table orders and refuses rewrites;
`test_catalog_version_endpoint.py` proves the HTTP surface. This is the piece
between them: rows become domain entities, publication order decides what
"current" means, and no driver exception escapes the port.

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
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tcg_api.catalog.versions import (
    VERSION_NAMESPACE,
    PostgresCardDatabaseVersionRepository,
    register_version,
    version_id,
)
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import CatalogUnavailable, InvalidCatalogRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to read",
)

GENERATED_AT = datetime(2026, 8, 18, tzinfo=UTC)


def record(version: str, **overrides: object) -> CardDatabaseVersion:
    fields: dict[str, object] = {
        "version": version,
        "source": "manual",
        "generated_at": GENERATED_AT,
        "set_count": 4,
        "card_count": 22,
        "external_id_count": 23,
    }
    fields.update(overrides)
    return CardDatabaseVersion(**fields)  # type: ignore[arg-type]


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Derived identifiers — no database needed
# ---------------------------------------------------------------------------
def test_a_version_identifier_predicts_its_primary_key() -> None:
    """Derived rather than random, so a retried write produces the same row."""
    assert version_id("pokemon-catalog-v0.3.0") == uuid.uuid5(
        VERSION_NAMESPACE, "pokemon-catalog-v0.3.0"
    )


def test_two_identifiers_do_not_share_a_key() -> None:
    assert version_id("pokemon-catalog-v0.3.0") != version_id("pokemon-catalog-v0.4.0")


# ---------------------------------------------------------------------------
# Against a real database
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base`, and order is not promised."""
    if not DATABASE_URL:
        return
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def empty_versions():
    """TRUNCATE, because the table's trigger refuses DELETE."""
    if not DATABASE_URL:
        yield
        return

    async def truncate() -> None:
        engine = create_async_engine(DATABASE_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("TRUNCATE card_database_versions RESTART IDENTITY CASCADE")
                )
        finally:
            await engine.dispose()

    run(truncate)
    yield
    run(truncate)


def published(*records: CardDatabaseVersion) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                for one in records:
                    await register_version(connection, one)
        finally:
            await engine.dispose()

    run(scenario)


#: Both port methods answer the same shape, so this is concrete rather than
#: generic. It also keeps `T` out of a nested function's annotations, which
#: CodeQL reads as a local used before assignment — PEP 695 type parameters are
#: not yet modelled, and the alert would be noise on every future helper.
Read = Callable[[PostgresCardDatabaseVersionRepository], Awaitable[CardDatabaseVersion | None]]


def reading(work: Read) -> CardDatabaseVersion | None:
    async def scenario() -> CardDatabaseVersion | None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                return await work(PostgresCardDatabaseVersionRepository(session))
        finally:
            await engine.dispose()

    return run(scenario)


@pytest.mark.integration
@requires_postgres
def test_an_unversioned_catalog_answers_with_absence(empty_versions) -> None:
    """Migrated but never seeded. Whether that is a failure is the caller's call."""
    assert reading(lambda repository: repository.current()) is None


@pytest.mark.integration
@requires_postgres
def test_the_current_version_is_the_most_recently_published(empty_versions) -> None:
    published(record("pokemon-catalog-v0.2.0"), record("pokemon-catalog-v0.10.0"))

    current = reading(lambda repository: repository.current())

    assert current is not None
    assert current.version == "pokemon-catalog-v0.10.0"


@pytest.mark.integration
@requires_postgres
def test_a_row_comes_back_as_a_validated_entity(empty_versions) -> None:
    """Constructing the entity is the validation, on the way out as well as in."""
    published(
        record(
            "pokemon-catalog-v0.3.0",
            source="tcgdex",
            source_license="MIT",
            source_revision="8f2c1ab",
            metadata={"upstream_repository": "tcgdex/cards-database"},
        )
    )

    current = reading(lambda repository: repository.current())

    assert current == CardDatabaseVersion(
        version="pokemon-catalog-v0.3.0",
        source="tcgdex",
        generated_at=GENERATED_AT,
        set_count=4,
        card_count=22,
        external_id_count=23,
        source_license="MIT",
        source_revision="8f2c1ab",
        metadata={"upstream_repository": "tcgdex/cards-database"},
    )


@pytest.mark.integration
@requires_postgres
def test_a_historical_version_can_be_looked_up(empty_versions) -> None:
    """What makes an old analysis re-derivable rather than re-guessable."""
    published(record("pokemon-catalog-v0.2.0"), record("pokemon-catalog-v0.10.0"))

    historical = reading(lambda repository: repository.get("pokemon-catalog-v0.2.0"))

    assert historical is not None
    assert historical.version == "pokemon-catalog-v0.2.0"


@pytest.mark.integration
@requires_postgres
def test_an_unpublished_identifier_answers_with_absence(empty_versions) -> None:
    published(record("pokemon-catalog-v0.2.0"))

    assert reading(lambda repository: repository.get("pokemon-catalog-v9.9.9")) is None


@pytest.mark.integration
@requires_postgres
def test_publishing_the_same_identifier_twice_writes_one_row(empty_versions) -> None:
    """`ON CONFLICT DO NOTHING`. The second run is not an error and not a rewrite."""
    published(record("pokemon-catalog-v0.2.0"))
    published(record("pokemon-catalog-v0.2.0", card_count=999))

    current = reading(lambda repository: repository.current())

    assert current is not None
    assert current.card_count == 22


@pytest.mark.integration
@requires_postgres
def test_a_row_that_violates_the_grammar_does_not_escape_as_data(empty_versions) -> None:
    """Written round the adapter, because the adapter would never write it.

    If a future migration or a manual fix put a moving pointer in the table, the
    read side must fail with the domain's own message rather than hand a caller
    an identifier that names nothing.
    """

    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "INSERT INTO card_database_versions "
                        "(id, version, source, generated_at, set_count, card_count, "
                        " external_id_count) "
                        "VALUES (:id, 'latest', 'manual', :at, 0, 0, 0)"
                    ),
                    {"id": uuid.uuid4(), "at": GENERATED_AT},
                )
        finally:
            await engine.dispose()

    run(scenario)

    with pytest.raises(InvalidCatalogRecord, match="moving pointer"):
        reading(lambda repository: repository.current())


def test_a_driver_failure_becomes_the_ports_own_error() -> None:
    """No asyncpg exception escapes the port — the rule `errors.py` states.

    Needs no database, and could not use one: the failure being tested is the
    one where there is nothing to connect to. Port 1 refuses immediately.
    """

    async def scenario() -> None:
        engine = create_async_engine("postgresql+asyncpg://tcg:tcg@127.0.0.1:1/tcg")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                repository = PostgresCardDatabaseVersionRepository(session)
                with pytest.raises(CatalogUnavailable):
                    await repository.current()
        finally:
            await engine.dispose()

    run(scenario)
