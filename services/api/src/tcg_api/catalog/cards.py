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

**What `search` decides.** The prefix runs against `card_number_key` with the
*input* normalised the same way the stored value is, so `25`, `025` and
`025/165` all find the card printed `025/165`. The name match is a
case-insensitive substring rather than a `LIKE`, so there are no metacharacters
to escape and nothing relies on ASCII folding — Japanese has neither case nor a
fold. The total and the window come from one statement, so the count cannot
describe a different catalog from the rows it counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.errors import CatalogUnavailable, InvalidCardSearch
from tcg_domain.repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    CardPage,
    CardQuery,
)

from tcg_api.catalog.tables import card_external_ids, cards, sets

__all__ = ["CARD_SELECT", "PostgresCardRepository", "card_entity"]


#: How a card reaches its set, written once.
#:
#: An inner join, and it cannot lose a row: `cards.set_id` is NOT NULL and
#: `fk_cards_set_id_game_language_sets` guarantees the set exists. A LEFT JOIN
#: would suggest a card without a set is representable, which is exactly what
#: the composite foreign key exists to deny.
#:
#: Hoisted because `search` counts through the same join it reads through, and
#: `sa.join(cards, sets)` without an onclause would infer the *composite* key
#: and join on three columns where this joins on one. The two are equivalent
#: only for as long as that constraint holds, which is precisely the kind of
#: agreement two statements should not have to reach separately.
_CARDS_AND_SETS: Final = sa.join(cards, sets, cards.c.set_id == sets.c.id)


#: Every column a :class:`~tcg_domain.catalog.Card` and its
#: :class:`~tcg_domain.catalog.Set` need, and none it does not — `card_number_key`
#: is a search key rather than a fact about the card, and `image_front` /
#: `image_back` stay NULL in V1 (ADR 0004).
#:
#: The set's columns are labelled because four of them collide with the card's:
#: both tables have `id`, `name` and `metadata`, and SQLAlchemy would otherwise
#: hand back whichever came last. `search` selects from this same statement, so
#: the two endpoints cannot disagree about what a card is.
CARD_SELECT: Final = sa.select(
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
).select_from(_CARDS_AND_SETS)


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


def _card_number_key(card_number: str) -> str:
    """Normalise what a user typed the way the stored column is normalised.

    `cards.card_number_key` is generated as
    `ltrim(split_part(card_number, '/', 1), '0')`, so the row printed "025/165"
    is keyed "25". Reducing the *input* the same way is what lets "25", "025"
    and "025/165" all reach it — the port promises a prefix match, and no prefix
    of "025/165" is "25".

    Returns the empty string for a number that is all zeros, which keys no card
    that exists. :func:`_conditions` turns that into a match on nothing rather
    than the prefix of everything.
    """
    return card_number.split("/")[0].lstrip("0")


def _conditions(query: CardQuery) -> list[sa.ColumnElement[bool]]:
    """The filters `query` names, ANDed. An absent filter is not applied.

    `game` and `language` are read off `cards` rather than `sets`: they are
    denormalised there and held true by `fk_cards_set_id_game_language_sets`, so
    the values cannot disagree and the filter can use
    `ix_cards_game_language_card_number_key` — which only pays when both are
    given, the prefix being on the trailing column.
    """
    conditions: list[sa.ColumnElement[bool]] = []

    if query.text is not None:
        # A case-insensitive substring, so a user typing "charizard" finds
        # Charizard. `strpos` rather than LIKE because a name fragment is
        # user-supplied text and LIKE would read "%" and "_" in it as wildcards;
        # this way there is nothing to escape. No ASCII folding: `lower` leaves
        # Japanese untouched, which has neither case nor a fold, and
        # `tables.py` already records that this match is a sequential scan — so
        # lowering costs no index that was being used.
        conditions.append(sa.func.strpos(sa.func.lower(cards.c.name), query.text.lower()) > 0)
    if query.game is not None:
        conditions.append(cards.c.game == query.game)
    if query.language is not None:
        conditions.append(cards.c.language == query.language)
    if query.set_code is not None:
        conditions.append(sets.c.set_code == query.set_code)
    if query.card_number is not None:
        key = _card_number_key(query.card_number)
        # An empty key is "0", "000" or "0/102" — a card that does not exist.
        # `startswith("")` would match the entire catalog, which is the opposite
        # of the answer. Expressed as a condition rather than an early return so
        # that this one input still travels the path every other one does and
        # can still report an unreachable catalog. Not an error: the domain
        # accepted "0" as a well-formed filter, and contradicting it here would
        # put the grammar in two places.
        conditions.append(
            # `autoescape` is load-bearing rather than decorative: "%" survives
            # normalisation intact, and unescaped it would match every card.
            cards.c.card_number_key.startswith(key, autoescape=True) if key else sa.false()
        )
    if query.variant is not None:
        conditions.append(cards.c.variant == query.variant)

    return conditions


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
        """Return the cards matching `query`, one window at a time.

        The bounds are checked here rather than left to the database, and
        checked *before* a statement is built. PostgreSQL reads `LIMIT -1` as
        "no limit", so a negative window would quietly fetch the whole catalog;
        a negative or over-large `OFFSET` raises a driver error instead, which
        :meth:`_execute` would report as `CatalogUnavailable` — a 503 blaming
        the catalog for a caller's arithmetic. Neither failure is one the port
        should be able to produce.

        The count travels with the rows as `count(*) OVER ()`. Window functions
        are evaluated before `LIMIT`, so it counts every match rather than the
        page, and because it is the same statement it is also the same snapshot:
        `offset + len(cards)` cannot exceed a `total` read a moment earlier from
        a catalog that has since changed. The price is that the window has to
        consume every matching row before emitting the first, so `LIMIT` cannot
        stop early — acceptable here for the same reason `tables.py` accepts a
        sequential scan on the name, and revisited with measurements if the
        catalog outgrows it.

        A window that begins past the last match returns no rows, and then the
        count has nowhere to travel, so it is asked for separately. Nothing
        races there: an empty page constrains nothing about `total`.
        """
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise InvalidCardSearch(f"limit must lie in [1, {MAX_SEARCH_LIMIT}], got {limit}")
        if offset < 0:
            raise InvalidCardSearch(f"offset must not be negative, got {offset}")

        conditions = _conditions(query)
        page = await self._execute(
            CARD_SELECT.add_columns(sa.func.count().over().label("total_matches"))
            .where(*conditions)
            # `(set_code, card_number, variant, id)`, the port's promise. Total,
            # so paging can neither drop nor duplicate a row; the trailing
            # identifier is what makes it so. `card_number` is COLLATE "C" text,
            # which means it sorts lexicographically — "15/102" comes before
            # "2/102". That is the documented order, not a defect to fix here.
            .order_by(
                sets.c.set_code,
                cards.c.card_number,
                cards.c.variant.nulls_last(),
                cards.c.id,
            )
            .limit(limit)
            .offset(offset)
        )
        rows = page.all()
        total = rows[0].total_matches if rows else await self._count(conditions)
        return CardPage(
            cards=tuple(card_entity(row) for row in rows),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def _count(self, conditions: Sequence[sa.ColumnElement[bool]]) -> int:
        """How many cards match, when the page itself could not say.

        Counts through `_CARDS_AND_SETS`, the same join the rows are read
        through, so "a card" means one thing in both statements.
        """
        result = await self._execute(
            sa.select(sa.func.count()).select_from(_CARDS_AND_SETS).where(*conditions)
        )
        return int(result.scalar_one())

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
