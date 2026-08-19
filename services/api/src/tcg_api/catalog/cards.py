"""Reading the canonical card catalog — the PostgreSQL side of `CardRepository`.

`tables.py` says what a card row *is*; this module is the only place that reads
one. The split is `versions.py`'s, for the same reason: DDL in one file,
statements in another, and nothing in between returns a mapping.

**The adapter lives here rather than in `packages/domain`.** ADR 0001 keeps the
domain package stdlib-only so every ML module can import it without dragging a
database driver along, and
:class:`tcg_domain.repository.CardRepository` names no `dsn`, `pool` or
`session` precisely so that this file can.

**Rows become entities.** Constructing :class:`~tcg_domain.catalog.Set` and then
:class:`~tcg_domain.catalog.Card` *is* the validation, on the way out as well as
on the way in. A row that somehow violates the domain's grammar — a padded name
written round this module by a migration or a manual fix — fails with
:class:`~tcg_domain.errors.InvalidCatalogRecord` rather than reaching a caller as
data.

**`search` is #28's.** This module is #29, whose job is the card detail endpoint
and the adapter it needs. The prefix match on `card_number_key`, the substring
name match that has to work for Japanese, the total order and the count are
`search`'s own design and are reviewed there.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.errors import CatalogUnavailable
from tcg_domain.repository import DEFAULT_SEARCH_LIMIT, CardPage, CardQuery

from tcg_api.catalog.tables import card_external_ids, cards, sets

__all__ = ["CARD_SELECT", "PostgresCardRepository", "card_entity"]


#: Every column a :class:`~tcg_domain.catalog.Card` and its
#: :class:`~tcg_domain.catalog.Set` need, and none it does not — `card_number_key`
#: is a search key rather than a fact about the card, and `image_front` /
#: `image_back` stay NULL in V1 (ADR 0004).
#:
#: The set's columns are labelled because four of them collide with the card's:
#: both tables have `id`, `name` and `metadata`, and SQLAlchemy would otherwise
#: hand back whichever came last. #28 selects from this same statement, so the
#: two endpoints cannot disagree about what a card is.
CARD_SELECT: Final = (
    sa.select(
        cards.c.id,
        cards.c.card_number,
        cards.c.name,
        cards.c.rarity,
        cards.c.variant,
        cards.c.metadata,
        sets.c.id.label("set_id"),
        sets.c.game.label("set_game"),
        sets.c.language.label("set_language"),
        sets.c.set_code,
        sets.c.name.label("set_name"),
        sets.c.release_date,
        sets.c.metadata.label("set_metadata"),
    )
    .select_from(cards)
    # An inner join, and it cannot lose a row: `cards.set_id` is NOT NULL and
    # `fk_cards_set_id_game_language_sets` guarantees the set exists. A LEFT
    # JOIN would suggest a card without a set is representable, which is exactly
    # what the composite foreign key exists to deny.
    .join(sets, cards.c.set_id == sets.c.id)
)


def card_entity(row: sa.Row[Any]) -> Card:
    """Build the domain entity a row describes.

    A card holds its :class:`~tcg_domain.catalog.Set` rather than a set id
    (#24), so the set is constructed first. `game` and `language` are read off
    the set for the same reason the entity reads them there: a card cannot
    disagree with its own set, and here the composite foreign key has already
    made that true in the database.
    """
    return Card(
        id=CardId(row.id),
        set=Set(
            id=SetId(row.set_id),
            game=row.set_game,
            language=row.set_language,
            set_code=row.set_code,
            name=row.set_name,
            release_date=row.release_date,
            metadata=row.set_metadata,
        ),
        card_number=row.card_number,
        name=row.name,
        rarity=row.rarity,
        variant=row.variant,
        metadata=row.metadata,
    )


def _external_id_entity(row: sa.Row[Any]) -> CardExternalId:
    return CardExternalId(
        card_id=CardId(row.card_id),
        provider=row.provider,
        external_id=row.external_id,
        metadata=row.metadata,
    )


class PostgresCardRepository:
    """The PostgreSQL side of :class:`~tcg_domain.repository.CardRepository`.

    Holds a session for the life of one request rather than an engine, so a
    caller that reads a card and then its provider identifiers reads both in the
    same transaction and cannot see the catalog change between the two.

    Satisfies the Protocol structurally — it does not subclass it, and nothing
    here imports FastAPI. Swapping this for another adapter changes no caller.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, card_id: CardId) -> Card | None:
        """Return the card with this identifier, or None if there is none.

        Absence is an answer rather than a failure: the caller asked whether a
        card exists and now knows. The HTTP 404 belongs to the endpoint, which
        is the layer that knows the question arrived over HTTP.
        """
        result = await self._execute(CARD_SELECT.where(cards.c.id == card_id))
        row = result.one_or_none()
        return None if row is None else card_entity(row)

    async def search(
        self,
        query: CardQuery,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> CardPage:
        """Not implemented here. `GET /cards/search` is issue #28.

        #29 carries this adapter because it is `CardRepository`'s first
        consumer, not because it needs every method. What search has to decide —
        the prefix match against `card_number_key`, a substring name match that
        works for Japanese without ASCII folding, the total order
        `(set_code, card_number, variant, id)` and the count that makes
        :class:`~tcg_domain.repository.CardPage` truthful — is #28's design and
        belongs in #28's review.

        Raising is deliberate rather than returning an empty page: an empty page
        is a valid answer meaning "nothing matched", and answering it here would
        make a missing implementation indistinguishable from a real miss.
        """
        raise NotImplementedError(
            "PostgresCardRepository.search is issue #28 (GET /cards/search). "
            "#29 implements get() and external_ids() only."
        )

    async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
        """Return every provider identifier recorded for this card.

        Empty when the card exists but no source has claimed it, and empty when
        the card does not exist — a caller that needs to tell those apart calls
        :meth:`get`.

        Ordered by `(provider, external_id)` so a response is stable between
        requests. Never deduplicated by provider:
        `ix_card_external_ids_provider_external_id` is deliberately not unique
        (#23), because a provider that does not split printing variants issues
        one identifier for what this catalog holds as several cards, and the
        reverse of that — one card carrying several identifiers from one
        provider — is equally legitimate.
        """
        statement = (
            sa.select(
                card_external_ids.c.card_id,
                card_external_ids.c.provider,
                card_external_ids.c.external_id,
                card_external_ids.c.metadata,
            )
            .where(card_external_ids.c.card_id == card_id)
            .order_by(card_external_ids.c.provider, card_external_ids.c.external_id)
        )
        result = await self._execute(statement)
        return tuple(_external_id_entity(row) for row in result)

    async def _execute(self, statement: sa.Select[Any]) -> sa.Result[Any]:
        # Every driver failure becomes `CatalogUnavailable` here, so no asyncpg
        # exception escapes the port and swapping this adapter for another
        # changes no caller's error handling.
        #
        # `OSError` alongside `SQLAlchemyError` because a refused connection
        # never becomes a SQLAlchemy error at all: asyncpg opens the socket
        # through asyncio, which raises `ConnectionRefusedError` before the
        # dialect has anything to wrap. That is precisely the case this port
        # exists to name, so it must not be the one that escapes.
        try:
            return await self._session.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            raise CatalogUnavailable("The card catalog could not be reached.") from error
