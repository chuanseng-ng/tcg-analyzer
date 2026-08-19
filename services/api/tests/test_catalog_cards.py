"""The PostgreSQL side of `CardRepository` — issue #29.

`test_card_endpoint.py` proves the HTTP surface against a fake; this is the
piece under it: rows become validated domain entities, absence is an answer
rather than a failure, and no driver exception escapes the port.

The fixtures are loaded through the real seed loader rather than hand-written
rows, so this suite tests the catalog the product actually ships with, and card
identifiers come from `seed_card_id` — derived, so nothing here has to look one
up.

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
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tcg_api.catalog.cards import PostgresCardRepository
from tcg_api.catalog.seed import apply_seed_catalog, load_seed_catalog, seed_card_id, seed_set_id
from tcg_domain.catalog import CardId
from tcg_domain.errors import CatalogUnavailable, InvalidCatalogRecord
from tcg_domain.repository import CardQuery

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to read",
)

#: The one fixture carrying two providers, which is what makes it the right card
#: for the external-id tests. See `database/seeds/catalog/cards.json`.
CHARIZARD = seed_card_id("pokemon", "en", "BS", "4/102", "unlimited-holo")

#: A Japanese fixture, so the adapter is exercised on text that has no ASCII
#: folding to fall back on.
JAPANESE_SET = seed_set_id("pokemon", "ja", "SV2a")

UNKNOWN = CardId(uuid.UUID("00000000-0000-5000-8000-000000000000"))


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def seeded() -> None:
    """Migrate and seed once for the module.

    `test_migrations.py` leaves the database at `base`, and pytest promises no
    ordering between files, so neither step can be assumed. The loader is
    idempotent, so running it again over an already-seeded database is a no-op
    rather than a conflict.
    """
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

    async def load() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            await apply_seed_catalog(load_seed_catalog(), engine)
        finally:
            await engine.dispose()

    run(load)


#: What `reading` accepts and answers, spelled without a type parameter.
#:
#: `test_catalog_versions.py` hit this first: CodeQL reads a PEP 695 type
#: parameter used inside a *nested* function's annotation as a local used before
#: assignment (`py/uninitialized-local-variable`), because it does not yet model
#: PEP 695. `run` above keeps its parameter, since nothing nests inside it.
#:
#: That file could stay precise, because both of its port methods answer the
#: same shape. `get` and `external_ids` do not, so one alias cannot be precise
#: for both, and these tests assert on the value rather than on its static type.
Read = Callable[[PostgresCardRepository], Awaitable[Any]]


def reading(work: Read) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                return await work(PostgresCardRepository(session))
        finally:
            await engine.dispose()

    return run(scenario)


def execute(statement: sa.Executable, parameters: dict[str, Any] | None = None) -> None:
    """Write round the adapter, which is the only way to produce a bad row."""

    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement, parameters)
        finally:
            await engine.dispose()

    run(scenario)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_seeded_card_comes_back_as_a_validated_entity() -> None:
    card = reading(lambda repository: repository.get(CHARIZARD))

    assert card is not None
    assert card.id == CHARIZARD
    assert card.name == "Charizard"
    assert card.card_number == "4/102"
    assert card.rarity == "Rare Holo"
    assert card.variant == "unlimited-holo"


@pytest.mark.integration
@requires_postgres
def test_a_card_arrives_holding_its_set() -> None:
    """#24: a card holds its `Set`, not a `set_id`. The join is the adapter's job."""
    card = reading(lambda repository: repository.get(CHARIZARD))

    assert card is not None
    assert card.set.id == seed_set_id("pokemon", "en", "BS")
    assert card.set.set_code == "BS"
    assert card.set.name == "Base Set"
    assert card.set.release_date is not None
    assert card.set.metadata == {"total_cards": 102}


@pytest.mark.integration
@requires_postgres
def test_game_and_language_are_read_through_the_set() -> None:
    """A card cannot disagree with its own set — here the composite FK says so too."""
    card = reading(lambda repository: repository.get(CHARIZARD))

    assert card is not None
    assert card.game == "pokemon"
    assert card.language == "en"


@pytest.mark.integration
@requires_postgres
def test_a_card_yields_the_printed_reference_the_pipeline_speaks() -> None:
    """`CardReference` is the bridge out of the catalog — analysis and market use it."""
    card = reading(lambda repository: repository.get(CHARIZARD))

    assert card is not None
    reference = card.reference
    assert reference.set_code == "BS"
    assert reference.card_number == "4/102"
    assert reference.variant == "unlimited-holo"


@pytest.mark.integration
@requires_postgres
def test_japanese_text_survives_the_round_trip() -> None:
    """V1 ships Japanese, where nothing may rely on ASCII folding."""
    japanese = reading(
        lambda repository: repository.get(seed_card_id("pokemon", "ja", "SV2a", "025/165", None))
    )

    assert japanese is not None
    assert japanese.language == "ja"
    assert japanese.set.id == JAPANESE_SET
    assert japanese.name and japanese.name.isascii() is False


@pytest.mark.integration
@requires_postgres
def test_an_unknown_identifier_answers_with_absence() -> None:
    """Absence is an answer, not a failure. The 404 belongs to the endpoint."""
    assert reading(lambda repository: repository.get(UNKNOWN)) is None


# ---------------------------------------------------------------------------
# Provider identifiers
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_card_may_carry_several_providers_identifiers() -> None:
    """`(provider, external_id)` is indexed but not unique (#23), by design."""
    external_ids = reading(lambda repository: repository.external_ids(CHARIZARD))

    assert [(one.provider, one.external_id) for one in external_ids] == [
        ("example", "example-base-charizard"),
        ("manual", "bs-4-unlimited-holo"),
    ]


@pytest.mark.integration
@requires_postgres
def test_provider_identifiers_come_back_in_a_stable_order() -> None:
    """Ordered in SQL, so two requests cannot disagree about the same card."""
    first = reading(lambda repository: repository.external_ids(CHARIZARD))
    second = reading(lambda repository: repository.external_ids(CHARIZARD))

    assert [one.external_id for one in first] == [one.external_id for one in second]


@pytest.mark.integration
@requires_postgres
def test_an_unknown_card_has_no_provider_identifiers() -> None:
    """Empty for an absent card and empty for an unclaimed one — `get` tells them apart."""
    assert reading(lambda repository: repository.external_ids(UNKNOWN)) == ()


# ---------------------------------------------------------------------------
# What the port promises about failure
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_row_that_violates_the_grammar_does_not_escape_as_data() -> None:
    """Constructing the entity is the validation on the way out as well as in.

    Written round the adapter, because the adapter would never write it. If a
    migration or a manual fix put a padded name in the table, the read side must
    fail with the domain's own message rather than hand a caller a name with
    whitespace baked into it.
    """
    malformed = uuid.uuid5(uuid.NAMESPACE_URL, "tcg-analyzer/test/malformed-card")
    execute(
        sa.text(
            "INSERT INTO cards (id, game, language, set_id, card_number, name) "
            "VALUES (:id, 'pokemon', 'en', :set_id, '999/102', '  Padded  ') "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
        ),
        {"id": malformed, "set_id": seed_set_id("pokemon", "en", "BS")},
    )
    try:
        with pytest.raises(InvalidCatalogRecord):
            reading(lambda repository: repository.get(CardId(malformed)))
    finally:
        execute(sa.text("DELETE FROM cards WHERE id = :id"), {"id": malformed})


def test_a_driver_failure_becomes_the_ports_own_error() -> None:
    """No asyncpg exception escapes the port — the rule `errors.py` states.

    Needs no database, and could not use one: the failure being tested is the
    one where there is nothing to connect to. Port 1 refuses immediately, and a
    refused connection never becomes a SQLAlchemy error at all.
    """

    async def scenario() -> None:
        engine = create_async_engine("postgresql+asyncpg://tcg:tcg@127.0.0.1:1/tcg")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                repository = PostgresCardRepository(session)
                with pytest.raises(CatalogUnavailable):
                    await repository.get(UNKNOWN)
                with pytest.raises(CatalogUnavailable):
                    await repository.external_ids(UNKNOWN)
        finally:
            await engine.dispose()

    run(scenario)


# ---------------------------------------------------------------------------
# What this adapter deliberately does not do yet
# ---------------------------------------------------------------------------
def test_search_is_issue_28_and_says_so() -> None:
    """Raising, not returning an empty page.

    An empty page is a valid answer meaning "nothing matched"; answering it here
    would make a missing implementation indistinguishable from a real miss.
    """

    async def scenario() -> None:
        engine = create_async_engine("postgresql+asyncpg://tcg:tcg@127.0.0.1:1/tcg")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                with pytest.raises(NotImplementedError, match="#28"):
                    await PostgresCardRepository(session).search(CardQuery())
        finally:
            await engine.dispose()

    run(scenario)
