"""The card catalog port — what the application may ask of a card database.

Spec §2.4: "no marketplace, price provider, card database, or external API may
become a hard dependency of the core domain. All external data must pass
through provider interfaces." This is that interface for the catalog. It says
what may be asked; it says nothing about who answers. There is deliberately no
`dsn`, `pool`, `session` or `schema` below — those are an adapter's private
business, and a port that leaked one would make PostgreSQL part of the calling
contract, exactly as ``tcg_shared.storage.port`` refuses to name a bucket.

**This was the first port in `packages/domain`, and it belongs here rather than
in `packages/shared`.** ADR 0002 placed :class:`~tcg_shared.storage.port.ObjectStorage`
in `shared` because object storage has no domain knowledge — it moves bytes. A
card repository returns :class:`~tcg_domain.catalog.Card`, so it does, and a
port that speaks the domain's language lives with the domain. A Protocol costs
nothing at import time, so the package stays stdlib-only either way.

Every method is `async` because the API service is async throughout and a
blocking call on the event loop is an outage under load. An adapter over a
synchronous client offloads to a worker thread rather than forcing the port to
be synchronous.

Three operations, because three are what the product needs: fetch one card
(#29), search for candidates (#28), and read a card's provider identifiers,
which is how a support question about a wrong record gets answered. There is
no `set(...)` because a :class:`~tcg_domain.catalog.Card` carries its set, and
no write side because V1's only writer is the import pipeline (#26), which
owns its own transaction and does not go through here.

The contract below is read as a specification by two other issues: #23 builds
its indexes from the filters :class:`CardQuery` names and the ordering
:meth:`CardRepository.search` promises, and #28 exposes both as
``GET /cards/search``. Changing either is a change to those.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from tcg_domain.card import validated_identifier, validated_language, validated_slug
from tcg_domain.catalog import Card, CardExternalId, CardId
from tcg_domain.errors import InvalidCardSearch

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "CardPage",
    "CardQuery",
    "CardRepository",
]

#: How many cards a search returns when the caller does not say.
DEFAULT_SEARCH_LIMIT: Final = 20

#: The most a single search may return. Stated here rather than at the HTTP
#: edge so that every caller of the port is bounded, not merely every caller of
#: the endpoint — a page size is a database cost before it is a payload size.
MAX_SEARCH_LIMIT: Final = 100


@dataclass(frozen=True, slots=True)
class CardQuery:
    """What a user is looking for. Every filter is optional and ANDed.

    A filter that is absent is not applied; a filter that is present must say
    something. An empty string is therefore rejected rather than treated as a
    wildcard — ``set_code=""`` almost always means a form field was submitted
    blank, and silently matching every set is the worst available answer.

    An entirely empty query is legitimate: it browses the catalog, paginated.

    Args:
        text: A fragment of the card's name, in the card's own language. Must
            match Japanese, so an implementation cannot rely on ASCII folding.
        game: A lowercase slug.
        language: An ISO 639-1 code.
        set_code: The publisher's set identifier, as printed.
        card_number: What the user read off the card. Matched as a prefix,
            because the printed form may be ``025/102`` while the user types
            ``25`` — an exact match here would fail the common case.
        variant: A printing variant. Holo, reverse holo and 1st edition value
            differently, so a user who wants one of them can say so.

    Raises:
        InvalidCardSearch: If a filter is present but empty, padded or
            malformed.
    """

    text: str | None = None
    game: str | None = None
    language: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    variant: str | None = None

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        if self.game is not None:
            set_field(self, "game", validated_slug("game", self.game, error=InvalidCardSearch))
        if self.language is not None:
            set_field(self, "language", validated_language(self.language, error=InvalidCardSearch))
        for optional in ("text", "set_code", "card_number", "variant"):
            value = getattr(self, optional)
            if value is not None:
                set_field(
                    self, optional, validated_identifier(optional, value, error=InvalidCardSearch)
                )

    def __str__(self) -> str:
        applied = {
            name: getattr(self, name)
            for name in ("text", "game", "language", "set_code", "card_number", "variant")
            if getattr(self, name) is not None
        }
        return ", ".join(f"{name}={value!r}" for name, value in applied.items()) or "everything"


@dataclass(frozen=True, slots=True)
class CardPage:
    """One window onto a search result, and enough context to page through it.

    Args:
        cards: The cards in this window, in the repository's total order.
            Copied into a tuple on construction.
        total: How many cards matched the query in full, so a UI can show "1-20
            of 137" and know when to stop.
        limit: The window size that produced `cards`.
        offset: How many matches this window skipped.

    Raises:
        InvalidCardSearch: If the window does not describe the result set it
            claims — a negative bound, a limit outside
            ``[1, MAX_SEARCH_LIMIT]``, more cards than the limit allows, or a
            window reaching past `total`.
    """

    cards: Sequence[Card]
    total: int
    limit: int
    offset: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cards", _validated_cards(self.cards))
        for name in ("total", "limit", "offset"):
            _reject_non_integer(name, getattr(self, name))

        if self.total < 0:
            raise InvalidCardSearch(f"total must not be negative, got {self.total}")
        if self.offset < 0:
            raise InvalidCardSearch(f"offset must not be negative, got {self.offset}")
        if not 1 <= self.limit <= MAX_SEARCH_LIMIT:
            raise InvalidCardSearch(f"limit must lie in [1, {MAX_SEARCH_LIMIT}], got {self.limit}")
        if len(self.cards) > self.limit:
            raise InvalidCardSearch(
                f"a page of limit {self.limit} cannot hold {len(self.cards)} cards"
            )
        if self.offset + len(self.cards) > self.total:
            raise InvalidCardSearch(
                f"a page of {len(self.cards)} cards at offset {self.offset} reaches past "
                f"the {self.total} matches it reports"
            )

    def __str__(self) -> str:
        first = self.offset + 1 if self.cards else self.offset
        return f"{first}-{self.offset + len(self.cards)} of {self.total}"


def _validated_cards(value: object) -> tuple[Card, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise InvalidCardSearch(f"cards must be a sequence, got {type(value).__name__}")
    for card in value:
        if not isinstance(card, Card):
            raise InvalidCardSearch(f"a page holds Cards, got {type(card).__name__}")
    return tuple(value)


def _reject_non_integer(name: str, value: object) -> None:
    # `bool` first: it is an `int`, and a page of `limit=True` is a bug that
    # would otherwise pass every check below it.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCardSearch(f"{name} must be an integer, got {type(value).__name__}")


class CardRepository(Protocol):
    """The canonical catalog, however it happens to be stored.

    Implementations must raise only :mod:`tcg_domain.errors` types — in
    practice :class:`~tcg_domain.errors.CatalogUnavailable` — so swapping one
    for another changes no caller's error handling and no driver exception
    reaches a route handler.
    """

    async def get(self, card_id: CardId) -> Card | None:
        """Return the card with this identifier, or None if there is none.

        Absence is an answer, not a failure: a caller asked whether a card
        exists and now knows. The HTTP 404 belongs to the endpoint, which is
        the layer that knows the question arrived over HTTP.
        :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION` is deliberately
        not used — it means "we cannot tell", and here we can.

        Raises:
            CatalogUnavailable: If the catalog could not be reached.
        """

    async def search(
        self,
        query: CardQuery,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> CardPage:
        """Return the cards matching `query`, one window at a time.

        Results are ordered by ``(set_code, card_number, variant, id)`` — a
        **total** key, so no two cards tie and paging can neither drop nor
        duplicate a row. The trailing identifier is what makes it total; the
        first three are what make it useful to a human reading a result list.
        Relevance ranking is not implied and would be a deliberate later
        addition, because a ranking that varies between queries is not a
        stable sort.

        `text` matches a name fragment and must work for Japanese; `card_number`
        matches as a prefix. Those two are the search's real index requirements.

        Args:
            query: The filters to apply. An empty query browses the catalog.
            limit: Window size, at most :data:`MAX_SEARCH_LIMIT`.
            offset: How many matches to skip.

        Raises:
            InvalidCardSearch: If `limit` or `offset` is out of range.
            CatalogUnavailable: If the catalog could not be reached.
        """

    async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
        """Return every provider identifier recorded for this card.

        Empty when the card exists but no source has claimed it, and empty when
        the card does not exist: a caller that needs to tell those apart calls
        :meth:`get`. One canonical card may carry several, which is the point
        of spec §10's third table.

        Raises:
            CatalogUnavailable: If the catalog could not be reached.
        """
