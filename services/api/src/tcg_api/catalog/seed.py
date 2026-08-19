"""Load the hand-authored catalog seeds into PostgreSQL.

Usage::

    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    uv run tcg-seed-catalog

The fixtures under `database/seeds/catalog/` are a deliberately small, verifiable
catalog subset written by hand under the `manual` provider. They exist so that
the rest of M1 — the repository adapter, the search endpoint, the identification
UI — has something real to run against while the import pipeline is still ahead
of us. They are *not* an authoritative card database and must never be treated as
one; the canonical source is the decision recorded in
`docs/adr/0004-the-canonical-card-catalog-source.md`.

**Identifiers are derived, never authored.** A set's id is `uuid5` over
``game/language/set_code`` and a card's over
``game/language/set_code/card_number/variant``, both in :data:`NAMESPACE`. Two
things follow, and both are the point:

* The loader is idempotent without looking anything up. Running it twice writes
  the same primary keys, so the second run is an upsert onto the first rather
  than a duplicate catalog.
* Other tests can hard-code an id. `seed_set_id("pokemon", "en", "BS")` is a
  fact about the fixtures, computable without a database.

Seeds are data, not schema: this module creates nothing and drops nothing, and
it will not delete a row that has left the fixtures. Removing seed data is a
migration's job, not a loader's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import InvalidCatalogRecord

from tcg_api.catalog.tables import card_database_versions, card_external_ids, cards, sets
from tcg_api.catalog.versions import register_version
from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.logging import configure_logging

__all__ = [
    "NAMESPACE",
    "SEED_CATALOG_GENERATED_AT",
    "SEED_CATALOG_SOURCE",
    "SEED_CATALOG_VERSION",
    "SeedCatalog",
    "apply_seed_catalog",
    "load_seed_catalog",
    "main",
    "seed_card_id",
    "seed_catalog_version",
    "seed_set_id",
]

logger = logging.getLogger(__name__)

#: The namespace every seeded identifier is derived in. A fixed, arbitrary UUID:
#: its only job is to keep these ids from colliding with anything else that
#: happens to use uuid5. Changing it re-keys the entire seed catalog, which would
#: orphan every row a previous run wrote — so do not.
NAMESPACE: Final = uuid.UUID("5777d37d-f725-4fff-9c94-ad299ff7f0c0")

#: Where the fixtures live, relative to a source checkout. `parents[5]` walks
#: `catalog/ -> tcg_api/ -> src/ -> api/ -> services/ -> <repo root>`.
DEFAULT_SEEDS_DIR: Final = Path(__file__).resolve().parents[5] / "database" / "seeds" / "catalog"

_SETS_FILE: Final = "sets.json"
_CARDS_FILE: Final = "cards.json"

#: The catalog version these fixtures publish. Explicit and ordered, never a
#: moving pointer (spec §31).
#:
#: **Bump it when the fixtures change materially.** A published version is
#: immutable — the loader writes it with `ON CONFLICT DO NOTHING` and the table
#: refuses an UPDATE outright — so adding or removing a fixture without bumping
#: this leaves a version record describing content it no longer matches. The
#: loader says so in a warning rather than guessing which of the two was meant.
SEED_CATALOG_VERSION: Final = "pokemon-catalog-seed-v0.0.0"

#: The fixtures' provider key, and the source their version names.
SEED_CATALOG_SOURCE: Final = "manual"

#: Fixed, and deliberately not `now()`. A seed that stamped the clock would make
#: two fresh databases disagree about the same immutable version, and no test
#: could assert what it holds. This is the day the fixtures were written; move it
#: with `SEED_CATALOG_VERSION`, never on its own.
SEED_CATALOG_GENERATED_AT: Final = datetime(2026, 8, 18, tzinfo=UTC)


def seed_set_id(game: str, language: str, set_code: str) -> SetId:
    """The identifier a seeded set will have, computable without a database."""
    return SetId(uuid.uuid5(NAMESPACE, f"{game}/{language}/{set_code}"))


def seed_card_id(
    game: str, language: str, set_code: str, card_number: str, variant: str | None
) -> CardId:
    """The identifier a seeded card will have.

    `variant` is part of the key because holo, reverse holo and 1st edition are
    economically different cards and therefore different rows. A card with no
    variant contributes an empty final segment rather than being keyed
    differently, so adding a variant to an existing fixture creates a new card
    instead of silently rewriting the old one.
    """
    return CardId(
        uuid.uuid5(NAMESPACE, f"{game}/{language}/{set_code}/{card_number}/{variant or ''}")
    )


@dataclass(frozen=True, slots=True)
class SeedCatalog:
    """The fixtures, parsed and validated, before anything touches a database.

    Holding domain entities rather than dictionaries means the fixtures are
    checked by the same validators the rest of the system uses: a malformed set
    code or a padded name fails here, at load time, with a message naming the
    file — not as a constraint violation halfway through a transaction.
    """

    sets: Sequence[Set]
    cards: Sequence[Card]
    external_ids: Sequence[CardExternalId]


class SeedError(RuntimeError):
    """A fixture file is missing, unparseable, or refers to something absent.

    Distinct from :class:`~tcg_domain.errors.InvalidCatalogRecord`, which means a
    record is malformed. This means the *set* of records does not hang together.
    """


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SeedError(
            f"{path} does not exist. Point --seeds-dir at the directory holding "
            f"{_SETS_FILE} and {_CARDS_FILE}."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SeedError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(payload, list):
        raise SeedError(f"{path} must hold a JSON array, got {type(payload).__name__}")
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise SeedError(f"{path}[{index}] must be a JSON object, got {type(entry).__name__}")
    return payload


def _parse_release_date(value: object, where: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SeedError(f"{where}: release_date must be an ISO date string, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SeedError(f"{where}: release_date is not an ISO date: {value!r}") from error


def _parse_metadata(value: object, where: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SeedError(f"{where}: metadata must be an object, got {type(value).__name__}")
    return value


def _require(entry: Mapping[str, Any], field: str, where: str) -> Any:
    if field not in entry:
        raise SeedError(f"{where}: missing required field {field!r}")
    return entry[field]


def load_seed_catalog(directory: Path | None = None) -> SeedCatalog:
    """Read and validate the fixtures. Touches no database.

    Raises:
        SeedError: If a file is missing or malformed, or a card names a set that
            the fixtures do not define.
        InvalidCatalogRecord: If a record is not a legal domain entity.
    """
    directory = directory or DEFAULT_SEEDS_DIR

    parsed_sets: dict[str, Set] = {}
    for index, entry in enumerate(_read_json(directory / _SETS_FILE)):
        where = f"{_SETS_FILE}[{index}]"
        game = _require(entry, "game", where)
        language = _require(entry, "language", where)
        set_code = _require(entry, "set_code", where)
        record = Set(
            id=seed_set_id(game, language, set_code),
            game=game,
            language=language,
            set_code=set_code,
            name=_require(entry, "name", where),
            release_date=_parse_release_date(entry.get("release_date"), where),
            metadata=_parse_metadata(entry.get("metadata"), where),
        )
        key = f"{game}/{language}/{set_code}"
        if key in parsed_sets:
            raise SeedError(f"{where}: {key} is defined twice")
        parsed_sets[key] = record

    parsed_cards: list[Card] = []
    parsed_external_ids: list[CardExternalId] = []
    for index, entry in enumerate(_read_json(directory / _CARDS_FILE)):
        where = f"{_CARDS_FILE}[{index}]"
        key = _require(entry, "set", where)
        if key not in parsed_sets:
            raise SeedError(
                f"{where}: no set {key!r} in {_SETS_FILE}. "
                f"Known sets: {', '.join(sorted(parsed_sets))}"
            )
        parent = parsed_sets[key]
        card_number = _require(entry, "card_number", where)
        variant = entry.get("variant")
        card = Card(
            id=seed_card_id(parent.game, parent.language, parent.set_code, card_number, variant),
            set=parent,
            card_number=card_number,
            name=_require(entry, "name", where),
            rarity=entry.get("rarity"),
            variant=variant,
            metadata=_parse_metadata(entry.get("metadata"), where),
        )
        parsed_cards.append(card)

        for position, external in enumerate(entry.get("external_ids", [])):
            external_where = f"{where}.external_ids[{position}]"
            if not isinstance(external, dict):
                raise SeedError(f"{external_where}: must be a JSON object")
            parsed_external_ids.append(
                CardExternalId(
                    card_id=card.id,
                    provider=_require(external, "provider", external_where),
                    external_id=_require(external, "external_id", external_where),
                    metadata=_parse_metadata(external.get("metadata"), external_where),
                )
            )

    _reject_duplicate_cards(parsed_cards)

    return SeedCatalog(
        sets=tuple(parsed_sets.values()),
        cards=tuple(parsed_cards),
        external_ids=tuple(parsed_external_ids),
    )


def _reject_duplicate_cards(records: Sequence[Card]) -> None:
    """Catch a repeated (set, number, variant) here rather than as an upsert.

    `uq_cards_set_id_card_number_variant` would catch it too, but an upsert keyed
    on a derived id would not: two identical fixtures collapse into one row and
    the file quietly disagrees with the database.
    """
    seen: set[uuid.UUID] = set()
    for card in records:
        if card.id in seen:
            raise SeedError(
                f"{card.set.set_code} {card.card_number} ({card.variant or 'no variant'}) "
                "appears twice in the fixtures"
            )
        seen.add(card.id)


def _set_rows(catalog: SeedCatalog) -> list[dict[str, Any]]:
    return [
        {
            "id": record.id,
            "game": record.game,
            "language": record.language,
            "set_code": record.set_code,
            "name": record.name,
            "release_date": record.release_date,
            "metadata": dict(record.metadata),
        }
        for record in catalog.sets
    ]


def _card_rows(catalog: SeedCatalog) -> list[dict[str, Any]]:
    # `card_number_key` is generated and `image_front` / `image_back` stay NULL
    # in V1 (ADR 0004), so none of the three appears here.
    return [
        {
            "id": record.id,
            "game": record.game,
            "language": record.language,
            "set_id": record.set.id,
            "card_number": record.card_number,
            "name": record.name,
            "rarity": record.rarity,
            "variant": record.variant,
            "metadata": dict(record.metadata),
        }
        for record in catalog.cards
    ]


def _external_id_rows(catalog: SeedCatalog) -> list[dict[str, Any]]:
    return [
        {
            # The domain does not model this surrogate key, so derive it from the
            # triple that *is* the identity — same reason as everywhere else here:
            # a second run must produce the same row, not another one.
            "id": uuid.uuid5(NAMESPACE, f"{record.card_id}/{record.provider}/{record.external_id}"),
            "card_id": record.card_id,
            "provider": record.provider,
            "external_id": record.external_id,
            "metadata": dict(record.metadata),
        }
        for record in catalog.external_ids
    ]


def seed_catalog_version(catalog: SeedCatalog) -> CardDatabaseVersion:
    """The version record these fixtures publish, built without a database.

    The counts come from the parsed fixtures rather than from `count(*)`: they
    describe what *this* run wrote, where a query would also count rows some
    other version wrote. #26 faces the same choice and should answer it the
    same way.

    `source_license` is None — the fixtures are this project's own text, and
    writing a licence there would be a lie. `source_revision` likewise: nothing
    was fetched, so there is no upstream revision to repeat.
    """
    return CardDatabaseVersion(
        version=SEED_CATALOG_VERSION,
        source=SEED_CATALOG_SOURCE,
        generated_at=SEED_CATALOG_GENERATED_AT,
        set_count=len(catalog.sets),
        card_count=len(catalog.cards),
        external_id_count=len(catalog.external_ids),
    )


async def _warn_if_the_published_version_no_longer_describes(
    connection: AsyncConnection, catalog: SeedCatalog
) -> None:
    """Say so when the fixtures have moved on and the identifier has not.

    `ON CONFLICT DO NOTHING` is what makes re-running safe; it is also what
    makes it silent. The catalog rows converge on the fixtures while the version
    record keeps describing the ones it was published with, and the operator is
    the only one who can decide whether that is a correction or a new version.
    """
    published = (
        await connection.execute(
            sa.select(
                card_database_versions.c.set_count,
                card_database_versions.c.card_count,
                card_database_versions.c.external_id_count,
            ).where(card_database_versions.c.version == SEED_CATALOG_VERSION)
        )
    ).one()

    loaded = (len(catalog.sets), len(catalog.cards), len(catalog.external_ids))
    if tuple(published) != loaded:
        logger.warning(
            "%s was published describing %s (sets, cards, external ids) but the "
            "fixtures now hold %s. A published version is never rewritten — bump "
            "SEED_CATALOG_VERSION.",
            SEED_CATALOG_VERSION,
            tuple(published),
            loaded,
        )


async def apply_seed_catalog(catalog: SeedCatalog, engine: AsyncEngine) -> None:
    """Write `catalog` to the database, in one transaction, idempotently.

    Upsert rather than insert-if-absent: correcting a name in a fixture and
    re-running should converge on the fixture, not leave the old value in place
    behind a silently skipped insert.

    Ordering is sets, then cards, then external ids, then the version record —
    the first three because the foreign keys require it, the version last
    because it counts what the others wrote. One transaction, so a failure
    halfway leaves nothing behind.
    """
    async with engine.begin() as connection:
        set_statement = insert(sets)
        await connection.execute(
            set_statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "set_code": set_statement.excluded.set_code,
                    "name": set_statement.excluded.name,
                    "release_date": set_statement.excluded.release_date,
                    "metadata": set_statement.excluded.metadata,
                },
            ),
            _set_rows(catalog),
        )

        card_statement = insert(cards)
        await connection.execute(
            card_statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": card_statement.excluded.name,
                    "rarity": card_statement.excluded.rarity,
                    "metadata": card_statement.excluded.metadata,
                    "updated_at": sa.func.now(),
                },
            ),
            _card_rows(catalog),
        )

        external_statement = insert(card_external_ids)
        await connection.execute(
            external_statement.on_conflict_do_update(
                index_elements=["card_id", "provider", "external_id"],
                set_={"metadata": external_statement.excluded.metadata},
            ),
            _external_id_rows(catalog),
        )

        await register_version(connection, seed_catalog_version(catalog))
        await _warn_if_the_published_version_no_longer_describes(connection, catalog)


async def seed(directory: Path | None = None) -> SeedCatalog:
    """Load the fixtures and apply them to the configured database."""
    catalog = load_seed_catalog(directory)
    engine = create_engine()
    try:
        await apply_seed_catalog(catalog, engine)
    finally:
        await engine.dispose()
    return catalog


def main() -> int:
    """Console-script entry point (`uv run tcg-seed-catalog`)."""
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--seeds-dir",
        type=Path,
        default=DEFAULT_SEEDS_DIR,
        help=f"directory holding {_SETS_FILE} and {_CARDS_FILE} (default: %(default)s)",
    )
    arguments = parser.parse_args()

    configure_logging(get_settings())

    try:
        catalog = asyncio.run(seed(arguments.seeds_dir))
    except (SeedError, InvalidCatalogRecord) as error:
        logger.error("catalog seed rejected: %s", error)
        return 1

    logger.info(
        "catalog seeded as %s: %d sets, %d cards, %d external ids",
        SEED_CATALOG_VERSION,
        len(catalog.sets),
        len(catalog.cards),
        len(catalog.external_ids),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
