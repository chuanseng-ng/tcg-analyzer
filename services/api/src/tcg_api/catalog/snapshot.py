"""The catalog interchange format, and the one path that writes a catalog.

A *snapshot* is a directory holding three files:

``sets.json``
    An array of sets: ``game``, ``language``, ``set_code``, ``name``, optional
    ``release_date`` and ``metadata``.
``cards.json``
    An array of cards, each naming its parent by the join key
    ``"{game}/{language}/{set_code}"`` and nesting its ``external_ids``.
``catalog.json``
    The provenance manifest — the spec §57 ``card_database_version`` this
    snapshot publishes, as data rather than as constants in a module.

`database/seeds/catalog/` is a snapshot without the manifest: the hand-authored
fixtures carry their provenance in :mod:`tcg_api.catalog.seed` instead, because
theirs is fixed and this project's own. Every other producer — today only the
TCGdex importer (#26) — writes all three.

**Identifiers are derived, never authored.** A set's id is `uuid5` over
``game/language/set_code`` and a card's over
``game/language/set_code/card_number/variant``, both in :data:`NAMESPACE`. Two
things follow, and both are the point:

* A loader is idempotent without looking anything up. Running it twice writes
  the same primary keys, so the second run is an upsert onto the first rather
  than a duplicate catalog. That also means *two sources* describing the same
  printing converge on one ``cards`` row carrying one ``card_external_ids`` row
  each — which is the property #26 has to demonstrate.
* Other tests can hard-code an id. ``set_id("pokemon", "en", "BS")`` is a fact
  about the fixtures, computable without a database.

This module creates no schema and drops nothing, and it will not delete a row
that has left a snapshot. Removing catalog data is a migration's job.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.catalog_version import CardDatabaseVersion

from tcg_api.catalog.tables import card_database_versions, card_external_ids, cards, sets
from tcg_api.catalog.versions import register_version

__all__ = [
    "CARDS_FILE",
    "DEFAULT_ADVICE",
    "DEFAULT_HINT",
    "DIGEST_KEY",
    "MANIFEST_FILE",
    "NAMESPACE",
    "SETS_FILE",
    "CatalogRecords",
    "SnapshotError",
    "apply_records",
    "card_id",
    "external_id_key",
    "read_manifest",
    "read_records",
    "records_digest",
    "reject_duplicate_cards",
    "set_id",
    "set_key",
    "verify_digest",
    "write_manifest",
    "write_records",
]

logger = logging.getLogger(__name__)

#: The namespace every catalog identifier is derived in. A fixed, arbitrary
#: UUID: its only job is to keep these ids from colliding with anything else
#: that happens to use uuid5. Changing it re-keys the entire catalog, which
#: would orphan every row a previous run wrote — so do not.
NAMESPACE: Final = uuid.UUID("5777d37d-f725-4fff-9c94-ad299ff7f0c0")

SETS_FILE: Final = "sets.json"
CARDS_FILE: Final = "cards.json"
MANIFEST_FILE: Final = "catalog.json"

#: Where a snapshot's digest is recorded, inside the manifest's `metadata`.
DIGEST_KEY: Final = "snapshot_digest"


def set_id(game: str, language: str, set_code: str) -> SetId:
    """The identifier a set will have, computable without a database."""
    return SetId(uuid.uuid5(NAMESPACE, f"{game}/{language}/{set_code}"))


def card_id(
    game: str, language: str, set_code: str, card_number: str, variant: str | None
) -> CardId:
    """The identifier a card will have.

    `variant` is part of the key because holo, reverse holo and 1st edition are
    economically different cards and therefore different rows. A card with no
    variant contributes an empty final segment rather than being keyed
    differently, so adding a variant to an existing record creates a new card
    instead of silently rewriting the old one.
    """
    return CardId(
        uuid.uuid5(NAMESPACE, f"{game}/{language}/{set_code}/{card_number}/{variant or ''}")
    )


def external_id_key(card: uuid.UUID, provider: str, external_id: str) -> uuid.UUID:
    """The surrogate key a `card_external_ids` row will have.

    The domain does not model this key, so it is derived from the triple that
    *is* the identity — same reason as everywhere else here: a second run must
    produce the same row, not another one.
    """
    return uuid.uuid5(NAMESPACE, f"{card}/{provider}/{external_id}")


@dataclass(frozen=True, slots=True)
class CatalogRecords:
    """A snapshot's records, parsed and validated, before anything touches a database.

    Holding domain entities rather than dictionaries means the records are
    checked by the same validators the rest of the system uses: a malformed set
    code or a padded name fails here, at load time, with a message naming the
    file — not as a constraint violation halfway through a transaction.
    """

    sets: Sequence[Set]
    cards: Sequence[Card]
    external_ids: Sequence[CardExternalId]

    @property
    def counts(self) -> tuple[int, int, int]:
        """(sets, cards, external ids) — what a version record has to describe."""
        return (len(self.sets), len(self.cards), len(self.external_ids))


class SnapshotError(RuntimeError):
    """A file is missing, unparseable, or refers to something absent.

    Distinct from :class:`~tcg_domain.errors.InvalidCatalogRecord`, which means a
    record is malformed. This means the *set* of records does not hang together.
    """


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
#: The flag a caller passes to point at a snapshot. Only used to make a
#: missing-file message actionable, and only overridden by the seed loader,
#: whose flag is spelled differently.
DEFAULT_HINT: Final = "--snapshot"


def _read_json(path: Path, hint: str) -> Any:
    if not path.is_file():
        raise SnapshotError(
            f"{path} does not exist. Point {hint} at a directory holding "
            f"{SETS_FILE} and {CARDS_FILE}."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SnapshotError(f"{path} is not valid JSON: {error}") from error


def _read_json_array(path: Path, hint: str) -> list[dict[str, Any]]:
    payload = _read_json(path, hint)
    if not isinstance(payload, list):
        raise SnapshotError(f"{path} must hold a JSON array, got {type(payload).__name__}")
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise SnapshotError(
                f"{path}[{index}] must be a JSON object, got {type(entry).__name__}"
            )
    return payload


def _parse_release_date(value: object, where: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SnapshotError(f"{where}: release_date must be an ISO date string, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SnapshotError(f"{where}: release_date is not an ISO date: {value!r}") from error


def _parse_metadata(value: object, where: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SnapshotError(f"{where}: metadata must be an object, got {type(value).__name__}")
    return value


def _require(entry: Mapping[str, Any], field: str, where: str) -> Any:
    if field not in entry:
        raise SnapshotError(f"{where}: missing required field {field!r}")
    return entry[field]


def set_key(record: Set) -> str:
    """The join key `cards.json` names a parent set by."""
    return f"{record.game}/{record.language}/{record.set_code}"


def read_records(directory: Path, *, hint: str = DEFAULT_HINT) -> CatalogRecords:
    """Read and validate `sets.json` and `cards.json`. Touches no database.

    Raises:
        SnapshotError: If a file is missing or malformed, or a card names a set
            the snapshot does not define.
        InvalidCatalogRecord: If a record is not a legal domain entity.
    """
    parsed_sets: dict[str, Set] = {}
    for index, entry in enumerate(_read_json_array(directory / SETS_FILE, hint)):
        where = f"{SETS_FILE}[{index}]"
        game = _require(entry, "game", where)
        language = _require(entry, "language", where)
        set_code = _require(entry, "set_code", where)
        record = Set(
            id=set_id(game, language, set_code),
            game=game,
            language=language,
            set_code=set_code,
            name=_require(entry, "name", where),
            release_date=_parse_release_date(entry.get("release_date"), where),
            metadata=_parse_metadata(entry.get("metadata"), where),
        )
        key = set_key(record)
        if key in parsed_sets:
            raise SnapshotError(f"{where}: {key} is defined twice")
        parsed_sets[key] = record

    parsed_cards: list[Card] = []
    parsed_external_ids: list[CardExternalId] = []
    for index, entry in enumerate(_read_json_array(directory / CARDS_FILE, hint)):
        where = f"{CARDS_FILE}[{index}]"
        key = _require(entry, "set", where)
        if key not in parsed_sets:
            raise SnapshotError(
                f"{where}: no set {key!r} in {SETS_FILE}. "
                f"Known sets: {', '.join(sorted(parsed_sets))}"
            )
        parent = parsed_sets[key]
        card_number = _require(entry, "card_number", where)
        variant = entry.get("variant")
        card = Card(
            id=card_id(parent.game, parent.language, parent.set_code, card_number, variant),
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
                raise SnapshotError(f"{external_where}: must be a JSON object")
            parsed_external_ids.append(
                CardExternalId(
                    card_id=card.id,
                    provider=_require(external, "provider", external_where),
                    external_id=_require(external, "external_id", external_where),
                    metadata=_parse_metadata(external.get("metadata"), external_where),
                )
            )

    reject_duplicate_cards(parsed_cards)

    return CatalogRecords(
        sets=tuple(parsed_sets.values()),
        cards=tuple(parsed_cards),
        external_ids=tuple(parsed_external_ids),
    )


def reject_duplicate_cards(records: Iterable[Card]) -> None:
    """Catch a repeated (set, number, variant) here rather than as an upsert.

    `uq_cards_set_id_card_number_variant` would catch it too, but an upsert keyed
    on a derived id would not: two identical records collapse into one row and
    the source quietly disagrees with the database.
    """
    seen: set[uuid.UUID] = set()
    for card in records:
        if card.id in seen:
            raise SnapshotError(
                f"{card.set.set_code} {card.card_number} ({card.variant or 'no variant'}) "
                "appears twice"
            )
        seen.add(card.id)


def read_manifest(directory: Path, *, hint: str = DEFAULT_HINT) -> CardDatabaseVersion:
    """Read `catalog.json` into the version record it describes.

    Raises:
        SnapshotError: If the file is missing or malformed.
        InvalidCatalogRecord: If the manifest is not a legal version record.
            `CardDatabaseVersion` does that validating, so a moving-pointer
            identifier or a naive timestamp is rejected here rather than by the
            database.
    """
    payload = _read_json(directory / MANIFEST_FILE, hint)
    where = MANIFEST_FILE
    if not isinstance(payload, dict):
        raise SnapshotError(f"{where} must hold a JSON object, got {type(payload).__name__}")

    generated_at = _require(payload, "generated_at", where)
    if not isinstance(generated_at, str):
        raise SnapshotError(f"{where}: generated_at must be an ISO timestamp string")
    try:
        moment = datetime.fromisoformat(generated_at)
    except ValueError as error:
        raise SnapshotError(f"{where}: generated_at is not an ISO timestamp") from error

    return CardDatabaseVersion(
        version=_require(payload, "version", where),
        source=_require(payload, "source", where),
        generated_at=moment,
        set_count=_require(payload, "set_count", where),
        card_count=_require(payload, "card_count", where),
        external_id_count=_require(payload, "external_id_count", where),
        source_license=payload.get("source_license"),
        source_revision=payload.get("source_revision"),
        metadata=_parse_metadata(payload.get("metadata"), where),
    )


# ---------------------------------------------------------------------------
# Writing the files
# ---------------------------------------------------------------------------
def _dump(payload: Any) -> str:
    # `ensure_ascii=False` so Japanese set and card names stay readable in the
    # file rather than becoming \uXXXX escapes; the file is UTF-8 and every
    # reader here says so. A trailing newline keeps the diff well-formed.
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_records(records: CatalogRecords, directory: Path) -> None:
    """Write `sets.json` and `cards.json`, in the shape `read_records` reads.

    The output is a function of the records alone — no clock, no ordering beyond
    the caller's own — so two runs over the same records produce byte-identical
    files. That is what makes :func:`records_digest` mean anything.
    """
    directory.mkdir(parents=True, exist_ok=True)

    by_card: dict[uuid.UUID, list[CardExternalId]] = {}
    for external in records.external_ids:
        by_card.setdefault(external.card_id, []).append(external)

    (directory / SETS_FILE).write_text(
        _dump(
            [
                {
                    "game": record.game,
                    "language": record.language,
                    "set_code": record.set_code,
                    "name": record.name,
                    "release_date": (
                        record.release_date.isoformat() if record.release_date else None
                    ),
                    "metadata": dict(record.metadata),
                }
                for record in records.sets
            ]
        ),
        encoding="utf-8",
    )

    (directory / CARDS_FILE).write_text(
        _dump(
            [
                {
                    "set": set_key(record.set),
                    "card_number": record.card_number,
                    "name": record.name,
                    "rarity": record.rarity,
                    "variant": record.variant,
                    "metadata": dict(record.metadata),
                    "external_ids": [
                        {
                            "provider": external.provider,
                            "external_id": external.external_id,
                            "metadata": dict(external.metadata),
                        }
                        for external in by_card.get(record.id, [])
                    ],
                }
                for record in records.cards
            ]
        ),
        encoding="utf-8",
    )


def records_digest(directory: Path) -> str:
    """A `sha256:` digest over the record files, as written.

    This is the handle that makes a re-load reproducible. The upstream source
    has no version stamp of its own, so what a run can honestly claim to have
    loaded is *these bytes*.
    """
    digest = hashlib.sha256()
    for name in (SETS_FILE, CARDS_FILE):
        path = directory / name
        if not path.is_file():
            raise SnapshotError(f"{path} does not exist; nothing to digest")
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def write_manifest(version: CardDatabaseVersion, directory: Path) -> None:
    """Write `catalog.json`.

    Call after :func:`write_records`: the digest a caller puts in the metadata is
    a digest of those files, and the manifest is deliberately not part of it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MANIFEST_FILE).write_text(
        _dump(
            {
                "version": version.version,
                "source": version.source,
                "source_license": version.source_license,
                "source_revision": version.source_revision,
                "generated_at": version.generated_at.isoformat(),
                "set_count": version.set_count,
                "card_count": version.card_count,
                "external_id_count": version.external_id_count,
                "metadata": dict(version.metadata),
            }
        ),
        encoding="utf-8",
    )


def verify_digest(version: CardDatabaseVersion, directory: Path) -> None:
    """Refuse a snapshot whose records no longer match the manifest describing them.

    Silent when the manifest records no digest: a producer that predates this
    key is not thereby corrupt, and `database/seeds/catalog/` is a hand-edited
    snapshot with no manifest at all.
    """
    recorded = version.metadata.get(DIGEST_KEY)
    if recorded is None:
        return
    actual = records_digest(directory)
    if recorded != actual:
        raise SnapshotError(
            f"{directory / MANIFEST_FILE} records {recorded} but {SETS_FILE} and "
            f"{CARDS_FILE} hash to {actual}. The snapshot has been edited since it "
            "was written; re-fetch it rather than publishing a version that "
            "describes content it no longer holds."
        )


# ---------------------------------------------------------------------------
# Writing to PostgreSQL
# ---------------------------------------------------------------------------
def _set_rows(records: CatalogRecords) -> list[dict[str, Any]]:
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
        for record in records.sets
    ]


def _card_rows(records: CatalogRecords) -> list[dict[str, Any]]:
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
        for record in records.cards
    ]


def _external_id_rows(records: CatalogRecords) -> list[dict[str, Any]]:
    return [
        {
            "id": external_id_key(record.card_id, record.provider, record.external_id),
            "card_id": record.card_id,
            "provider": record.provider,
            "external_id": record.external_id,
            "metadata": dict(record.metadata),
        }
        for record in records.external_ids
    ]


async def _warn_if_the_published_version_no_longer_describes(
    connection: AsyncConnection,
    records: CatalogRecords,
    version: CardDatabaseVersion,
    advice: str,
) -> None:
    """Say so when the records have moved on and the identifier has not.

    `ON CONFLICT DO NOTHING` is what makes re-running safe; it is also what
    makes it silent. The catalog rows converge on the source while the version
    record keeps describing the ones it was published with, and the operator is
    the only one who can decide whether that is a correction or a new version.
    """
    published = (
        await connection.execute(
            sa.select(
                card_database_versions.c.set_count,
                card_database_versions.c.card_count,
                card_database_versions.c.external_id_count,
            ).where(card_database_versions.c.version == version.version)
        )
    ).one()

    if tuple(published) != records.counts:
        logger.warning(
            "%s was published describing %s (sets, cards, external ids) but the "
            "source now holds %s. A published version is never rewritten — %s.",
            version.version,
            tuple(published),
            records.counts,
            advice,
        )


#: What to tell an operator whose records no longer match the version they are
#: publishing under. Overridden by the seed loader, whose fix is a constant.
DEFAULT_ADVICE: Final = "publish a new one"


async def apply_records(
    records: CatalogRecords,
    version: CardDatabaseVersion,
    engine: AsyncEngine,
    *,
    advice: str = DEFAULT_ADVICE,
) -> None:
    """Write `records` to the database, in one transaction, idempotently.

    Upsert rather than insert-if-absent: correcting a name upstream and
    re-running should converge on the source, not leave the old value in place
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
            _set_rows(records),
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
            _card_rows(records),
        )

        external_statement = insert(card_external_ids)
        await connection.execute(
            external_statement.on_conflict_do_update(
                index_elements=["card_id", "provider", "external_id"],
                set_={"metadata": external_statement.excluded.metadata},
            ),
            _external_id_rows(records),
        )

        await register_version(connection, version)
        await _warn_if_the_published_version_no_longer_describes(
            connection, records, version, advice
        )
