"""Reading and writing spec §57's card database version.

`tables.py` says what a version record *is*; this module is the only place that
reads or writes one. The split matches `seed.py`'s: DDL there, statements here.

The read side satisfies
:class:`tcg_domain.catalog_version.CardDatabaseVersionRepository`, so the API
depends on the port rather than on this module. The write side is a plain
function taking a connection, because both of V1's writers — the seed loader and
the import pipeline (#26) — already own the transaction that writes the catalog
a version describes, and the version belongs in it.

**Rows become entities.** Nothing here returns a mapping. Constructing
:class:`~tcg_domain.catalog_version.CardDatabaseVersion` is the validation, on
the way out as well as on the way in, which is the same trick `seed.py` plays on
fixtures: a row that somehow violates the grammar fails with the domain's own
message rather than reaching a caller as data.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from tcg_domain.catalog_version import CardDatabaseVersion
from tcg_domain.errors import CatalogUnavailable

from tcg_api.catalog.tables import card_database_versions
from tcg_api.database import execute

__all__ = [
    "VERSION_NAMESPACE",
    "PostgresCardDatabaseVersionRepository",
    "register_version",
    "version_id",
]

#: The namespace every version's primary key is derived in. Fixed forever:
#: changing it re-keys every row a previous run wrote, exactly as `seed.py`'s
#: own namespace warns. Distinct from that one so no derivation can collide.
VERSION_NAMESPACE: Final = uuid.UUID("6d0d6f2b-2b0a-5e6b-9a2f-1c1d0f8a4b73")


def version_id(version: str) -> uuid.UUID:
    """The primary key a version identifier will have, computable without a database.

    Derived rather than random so a writer that has to retry produces the same
    row rather than a second one, and so a test can predict an id.
    """
    return uuid.uuid5(VERSION_NAMESPACE, version)


def _entity(row: sa.Row[Any]) -> CardDatabaseVersion:
    return CardDatabaseVersion(
        version=row.version,
        source=row.source,
        generated_at=row.generated_at,
        set_count=row.set_count,
        card_count=row.card_count,
        external_id_count=row.external_id_count,
        source_license=row.source_license,
        source_revision=row.source_revision,
        metadata=row.metadata,
    )


_SELECT: Final = sa.select(
    card_database_versions.c.version,
    card_database_versions.c.source,
    card_database_versions.c.source_license,
    card_database_versions.c.source_revision,
    card_database_versions.c.generated_at,
    card_database_versions.c.set_count,
    card_database_versions.c.card_count,
    card_database_versions.c.external_id_count,
    card_database_versions.c.metadata,
)


class PostgresCardDatabaseVersionRepository:
    """The PostgreSQL side of `CardDatabaseVersionRepository`.

    Holds a session for the life of one request rather than an engine, so a
    caller that later reads a card in the same request reads it in the same
    transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self) -> CardDatabaseVersion | None:
        """The most recently published version, or None if there is none.

        Ordered by `ordinal` and never by `version`: under `COLLATE "C"`
        'pokemon-catalog-v0.10.0' sorts before 'pokemon-catalog-v0.3.0', so
        ordering on the identifier would answer with the older catalog.
        """
        statement = _SELECT.order_by(card_database_versions.c.ordinal.desc()).limit(1)
        row = await self._one_or_none(statement)
        return None if row is None else _entity(row)

    async def get(self, version: str) -> CardDatabaseVersion | None:
        """The version with this identifier, or None if it was never published."""
        statement = _SELECT.where(card_database_versions.c.version == version)
        row = await self._one_or_none(statement)
        return None if row is None else _entity(row)

    async def _one_or_none(self, statement: sa.Select[Any]) -> sa.Row[Any] | None:
        # Every driver failure becomes `CatalogUnavailable` here, so no asyncpg
        # exception escapes the port and swapping this adapter for another
        # changes no caller's error handling. `database.execute` is where that
        # translation lives for every store in this service.
        result = await execute(
            self._session,
            statement,
            unavailable=CatalogUnavailable,
            message="The card catalog could not be reached.",
        )
        return result.one_or_none()


async def register_version(
    connection: AsyncConnection,
    record: CardDatabaseVersion,
) -> None:
    """Publish `record`, unless its identifier has been published already.

    `ON CONFLICT DO NOTHING`, which reverses the policy `seed.py` applies to
    catalog content. Correcting a card's name and re-running should converge on
    the fixture; rewriting a published version would falsify every analysis that
    recorded it. The way to change what an identifier means is to publish a new
    one — and the table's trigger enforces that regardless, since `DO UPDATE`
    would issue the `UPDATE` it refuses.

    Takes a connection rather than opening one: the version and the rows it
    counts commit together, or neither does.
    """
    statement = insert(card_database_versions).values(
        id=version_id(record.version),
        version=record.version,
        source=record.source,
        source_license=record.source_license,
        source_revision=record.source_revision,
        generated_at=record.generated_at,
        set_count=record.set_count,
        card_count=record.card_count,
        external_id_count=record.external_id_count,
        metadata=dict(record.metadata),
    )
    # On `version`, not `id`. They are equivalent while the id is derived from
    # the identifier, and `version` stays the right answer if that ever changes.
    await connection.execute(statement.on_conflict_do_nothing(index_elements=["version"]))
