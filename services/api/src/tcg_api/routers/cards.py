"""The card catalog's HTTP surface — spec §64's `GET /cards/search` and
`GET /cards/{id}`.

Two endpoints, and they are the two halves of one flow: a user searches for the
card in their hand, then confirms the one they picked before any analysis
proceeds. Spec §6's Card block — name, set, card number, language, variant — is
what the detail endpoint answers, minus identification confidence, which belongs
to an analysis rather than to a catalog record. The search returns a smaller
record: enough to choose between candidates, and `id` to ask for the rest.

**Nothing matching is an empty page, never a 404.** The detail endpoint answers
404 because a caller named one card and this deployment cannot identify it; a
search that matches nothing has read the catalog correctly and found it holds no
such card. The two are different questions and they get different answers.

**No card images.** `docs/adr/0004-the-canonical-card-catalog-source.md` imports
none: TCGdex's MIT licence covers its compilation, not The Pokémon Company's
artwork. `cards.image_front` and `cards.image_back` are therefore always NULL,
and the response omits them rather than returning empty URLs — a field that can
only ever be empty is an invitation to render an empty image. The only card
images this product shows are the user's own uploads (M2).

The router holds HTTP and nothing else; the SQL lives in `tcg_api.catalog.cards`,
behind the domain's port, exactly as `routers/catalog.py` delegates to
`tcg_api.catalog.versions`.

**`/cards/search` is declared above `read_card`, and any further literal path
must be too.** FastAPI matches routes in declaration order, so a literal path
registered after `/{card_id}` is shadowed by it — and because `card_id` is typed
`UUID`, the symptom is a 422 complaining that the path segment is not a valid
UUID rather than an obviously missing route.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import AfterValidator, BaseModel, Field
from tcg_domain.card import validated_identifier, validated_language, validated_slug
from tcg_domain.catalog import Card, CardExternalId, CardId
from tcg_domain.errors import CatalogUnavailable, InvalidCardSearch
from tcg_domain.repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    CardPage,
    CardQuery,
    CardRepository,
)

from tcg_api.catalog.cards import PostgresCardRepository
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse

__all__ = [
    "CardExternalIdResponse",
    "CardResponse",
    "CardSearchResponse",
    "CardSetResponse",
    "CardSummaryResponse",
    "card_repository",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cards", tags=["cards"])

#: Reaching the database at all failed. The same message and the same
#: `details.reason` as `GET /catalog/version`, because it is the same condition
#: and a client should not have to learn two vocabularies for it.
_UNREACHABLE = "The card catalog could not be reached."

#: No card is recorded under the requested identifier.
#:
#: **`card_not_identified`, at 404.** Spec §66 names eight codes and no
#: `not_found`; adding a ninth is a specification change rather than a local
#: decision, which is the same reasoning that made `/catalog/version` answer 503
#: rather than invent one. The code is not a stretch here: a caller named a card
#: and this deployment cannot identify it. The status is overridden from the
#: taxonomy's default 422 because the request was well-formed — nothing about it
#: was unprocessable; the card simply is not in the catalog. A client tells this
#: apart from the identification pipeline's own `card_not_identified` by the
#: status code and the endpoint.
_UNKNOWN_CARD = "No card is recorded under that identifier."


class CardSetResponse(BaseModel):
    """The set a card was printed in, nested rather than linked.

    Every screen that shows a card shows its set — a card number without one
    identifies nothing — so a second request would be a round trip for data the
    first already had to join against.
    """

    id: UUID = Field(
        description="This catalog's identifier for the set. Never a provider's.",
    )
    set_code: str = Field(
        description="The publisher's set identifier, verbatim.",
        examples=["BS"],
    )
    name: str = Field(
        description="The set's printed name, in its own language.",
        examples=["Base Set"],
    )
    release_date: date | None = Field(
        description="The day the set went on sale, where it is known. A date, never a timestamp.",
        examples=["1999-01-09"],
    )
    metadata: dict[str, Any] = Field(
        description="Whatever the source carried that has no field of its own.",
        examples=[{"total_cards": 102}],
    )


class CardExternalIdResponse(BaseModel):
    """One external database's identifier for this card.

    Included because spec §10's third table is the seam that keeps catalog
    sources replaceable, and a support question about a wrong record is
    unanswerable without it. Several entries may share a `provider`: the index
    behind them is deliberately not unique (#23).
    """

    provider: str = Field(
        description="A lowercase slug naming the source — 'manual' or 'tcgdex' in V1.",
        examples=["manual"],
    )
    external_id: str = Field(
        description="The identifier as that provider issued it, verbatim.",
        examples=["bs-4-unlimited-holo"],
    )


class CardResponse(BaseModel):
    """The body of a successful `GET /cards/{id}` — spec §6's Card block.

    `apps/web` generates its types from this model (ADR 0001), so the field
    names are a public contract.

    Two absences are deliberate. `identification_confidence` belongs to an
    analysis, not to a catalog card, and putting it here would invite a client
    to read a catalog lookup as an identification. `image_front` / `image_back`
    are always NULL in V1 — see the module docstring and ADR 0004.

    `metadata` is carried even though `CatalogVersionResponse` omitted its own.
    The two are different cases: a version's metadata records how a run went,
    where a card's records facts about the card that have no field yet — the set
    total a "4/102" is read against, for instance — and #29 names it as part of
    the canonical record. It generates as an untyped record, which is the honest
    shape for a free-form field.
    """

    id: UUID = Field(
        description="This catalog's identifier for the card. Never a provider's.",
    )
    name: str = Field(
        description="The card's printed name, in its own language.",
        examples=["Charizard"],
    )
    card_number: str = Field(
        description="The number printed on the card, verbatim.",
        examples=["4/102"],
    )
    game: str = Field(
        description="A lowercase slug. 'pokemon' in V1, and a field rather than a constant.",
        examples=["pokemon"],
    )
    language: str = Field(
        description=(
            "An ISO 639-1 code, read through the set. Japanese sets are distinct "
            "sets with their own numbering, not translations."
        ),
        examples=["en"],
    )
    rarity: str | None = Field(
        description="The printed rarity, where the source records one.",
        examples=["Rare Holo"],
    )
    variant: str | None = Field(
        description=(
            "The printing variant. Economically load-bearing: holo, reverse holo "
            "and 1st edition trade at very different prices."
        ),
        examples=["unlimited-holo"],
    )
    metadata: dict[str, Any] = Field(
        description="Whatever the source carried that has no field of its own.",
        examples=[{}],
    )
    set: CardSetResponse
    external_ids: list[CardExternalIdResponse] = Field(
        description="Every external database identifier recorded for this card.",
    )


async def card_repository() -> AsyncIterator[CardRepository]:
    """Yield the repository for one request. A dependency so tests can override it.

    Building the session factory sits inside the guard for the reason
    `routers/catalog.py` gives: it reads `TCG_API_DATABASE_URL`, and an unset or
    malformed value should be the same 503 as an unreachable database rather
    than a 500. A deployment without a database configured has no catalog to
    read, which is not something unexpected having happened.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("catalog session factory could not be built", exc_info=True)
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "catalog_unreachable"},
        ) from error

    async with factory() as session:
        yield PostgresCardRepository(session)


def _response(card: Card, external_ids: Sequence[CardExternalId]) -> CardResponse:
    return CardResponse(
        id=card.id,
        name=card.name,
        card_number=card.card_number,
        game=card.game,
        language=card.language,
        rarity=card.rarity,
        variant=card.variant,
        metadata=dict(card.metadata),
        set=CardSetResponse(
            id=card.set.id,
            set_code=card.set.set_code,
            name=card.set.name,
            release_date=card.set.release_date,
            metadata=dict(card.set.metadata),
        ),
        external_ids=[
            CardExternalIdResponse(provider=external.provider, external_id=external.external_id)
            for external in external_ids
        ],
    )


# ---------------------------------------------------------------------------
# Search — spec §64's `GET /cards/search`
#
# The filter grammar is the domain's, not a second copy of it. Each validator
# below calls the same function `CardQuery` calls, so a rule cannot hold in one
# place and not the other. `InvalidCardSearch` is a `ValueError`, which pydantic
# turns into a request-validation error, so a blank or padded filter arrives as
# FastAPI's own 422 — the same treatment a malformed path identifier gets, and
# for the same reason: `tcg_api.errors` leaves request validation alone because
# a malformed query string is a transport-level failure with no §66 meaning.
#
# An omitted parameter never reaches a validator, so none of them needs to
# handle None; `?text=` — present but empty — does reach one, and is rejected.
# ---------------------------------------------------------------------------
def _identifier(field: str) -> AfterValidator:
    def check(value: str) -> str:
        return validated_identifier(field, value, error=InvalidCardSearch)

    return AfterValidator(check)


def _slug(field: str) -> AfterValidator:
    def check(value: str) -> str:
        return validated_slug(field, value, error=InvalidCardSearch)

    return AfterValidator(check)


def _language() -> AfterValidator:
    def check(value: str) -> str:
        return validated_language(value, error=InvalidCardSearch)

    return AfterValidator(check)


#: The most matches a caller may skip. `CardPage` bounds the window's size but
#: not where it starts, and an unbounded offset is not merely useless — a value
#: past PostgreSQL's `bigint` overflows in the driver, which would surface as a
#: 503 blaming the catalog for a number the caller chose. Generous enough that
#: no real catalog reaches it, small enough to stay a number.
MAX_SEARCH_OFFSET: Final = 1_000_000


class CardSummaryResponse(BaseModel):
    """One search result — enough to tell it apart from its neighbours.

    Deliberately smaller than `CardResponse`. `variant` is the reason this is
    not merely a name and a number: holo, reverse holo and 1st edition are
    different cards economically, and a user who picks the wrong one is given
    the wrong valuation later. `external_ids` is absent because it would be a
    query per result for something nobody chooses between cards on, and
    `metadata` because it generates as an untyped record and belongs to the
    detail view. No thumbnail: ADR 0004 imports no card images.

    `GET /cards/{id}` is where the rest lives; `id` is how a caller gets there.
    """

    id: UUID = Field(
        description="This catalog's identifier for the card. Never a provider's.",
    )
    name: str = Field(
        description="The card's printed name, in its own language.",
        examples=["Charizard"],
    )
    card_number: str = Field(
        description="The number printed on the card, verbatim.",
        examples=["4/102"],
    )
    game: str = Field(
        description="A lowercase slug. 'pokemon' in V1, and a field rather than a constant.",
        examples=["pokemon"],
    )
    language: str = Field(
        description="An ISO 639-1 code, read through the set.",
        examples=["en"],
    )
    rarity: str | None = Field(
        description="The printed rarity, where the source records one.",
        examples=["Rare Holo"],
    )
    variant: str | None = Field(
        description=(
            "The printing variant. Shown in results because it is economically "
            "load-bearing: holo, reverse holo and 1st edition trade at very "
            "different prices, so choosing between them is the point of a search."
        ),
        examples=["unlimited-holo"],
    )
    set: CardSetResponse


class CardSearchResponse(BaseModel):
    """The body of a successful `GET /cards/search`.

    `apps/web` generates its types from this model (ADR 0001), so the field
    names are a public contract.

    `total` counts every match rather than the page, so a UI can say "1-20 of
    137" and know when to stop. It is read in the same statement as the rows, so
    the two always describe one catalog.
    """

    cards: list[CardSummaryResponse] = Field(
        description="The matches in this window, in the catalog's total order.",
    )
    total: int = Field(
        description="How many cards matched in full, across every page.",
        examples=[137],
    )
    limit: int = Field(description="The window size that produced `cards`.", examples=[20])
    offset: int = Field(description="How many matches this window skipped.", examples=[0])


def _summary(card: Card) -> CardSummaryResponse:
    return CardSummaryResponse(
        id=card.id,
        name=card.name,
        card_number=card.card_number,
        game=card.game,
        language=card.language,
        rarity=card.rarity,
        variant=card.variant,
        set=CardSetResponse(
            id=card.set.id,
            set_code=card.set.set_code,
            name=card.set.name,
            release_date=card.set.release_date,
            metadata=dict(card.set.metadata),
        ),
    )


def _page_response(page: CardPage) -> CardSearchResponse:
    return CardSearchResponse(
        cards=[_summary(card) for card in page.cards],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


# Declared above `read_card`, and it must stay there. FastAPI matches routes in
# declaration order, so registering this after `/{card_id}` lets that route —
# whose parameter is typed `UUID` — swallow it, and the symptom is a 422
# complaining that "search" is not a valid UUID rather than a missing route.
@router.get(
    "/search",
    response_model=CardSearchResponse,
    summary="Find cards in the catalog",
    description=(
        "Searches the canonical catalog so a user can find the card in their "
        "hand. Every filter is optional and they are ANDed; an empty query "
        "browses the catalog. `text` matches a fragment of the printed name "
        "without regard to case, and works for Japanese. `card_number` matches "
        "as a prefix of the printed number's numerator, so `25`, `025` and "
        "`025/165` all find the card printed `025/165`. Results are ordered by "
        "`(set_code, card_number, variant, id)` — a total order, so paging "
        "neither drops nor duplicates a row. Nothing matching is an empty page, "
        "never a 404. No prices, and no images (ADR 0004)."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The card catalog could not be reached.",
        },
    },
)
async def search_cards(
    repository: Annotated[CardRepository, Depends(card_repository)],
    text: Annotated[
        str | None,
        Query(description="A fragment of the card's printed name. Case-insensitive."),
        _identifier("text"),
    ] = None,
    game: Annotated[
        str | None,
        Query(description="A lowercase slug — 'pokemon' in V1."),
        _slug("game"),
    ] = None,
    language: Annotated[
        str | None,
        Query(description="An ISO 639-1 code — 'en' or 'ja' in V1."),
        _language(),
    ] = None,
    set_code: Annotated[
        str | None,
        Query(description="The publisher's set identifier, as printed."),
        _identifier("set_code"),
    ] = None,
    card_number: Annotated[
        str | None,
        Query(description="What is printed on the card. Matched as a prefix."),
        _identifier("card_number"),
    ] = None,
    variant: Annotated[
        str | None,
        Query(description="A printing variant, e.g. 'reverse-holo'."),
        _identifier("variant"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_SEARCH_LIMIT, description="Window size."),
    ] = DEFAULT_SEARCH_LIMIT,
    offset: Annotated[
        int,
        Query(ge=0, le=MAX_SEARCH_OFFSET, description="How many matches to skip."),
    ] = 0,
) -> CardSearchResponse:
    """Return the cards matching the filters, one window at a time.

    There is no 404 here, and that is not an oversight. `GET /cards/{id}`
    answers one because a caller named a specific card and this deployment
    cannot identify it; a search that matches nothing has read the catalog
    correctly and found it holds no such card. An empty page is the answer,
    including when `offset` reaches past the last match.

    The filters are validated against the domain's own grammar before they get
    here, so `CardQuery` cannot reject what FastAPI accepted, and `limit` and
    `offset` are bounded by the route, so the repository's own range check is
    unreachable from HTTP — it guards the port's other callers.
    """
    query = CardQuery(
        text=text,
        game=game,
        language=language,
        set_code=set_code,
        card_number=card_number,
        variant=variant,
    )
    try:
        page = await repository.search(query, limit=limit, offset=offset)
    except CatalogUnavailable as error:
        logger.warning("cards could not be searched", exc_info=True)
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "catalog_unreachable"},
        ) from error

    return _page_response(page)


@router.get(
    "/{card_id}",
    response_model=CardResponse,
    summary="Return the canonical detail for one card",
    description=(
        "Returns everything the catalog records about one card — spec §6's Card "
        "block, apart from identification confidence, which belongs to an "
        "analysis rather than to a catalog record. No card images: ADR 0004 "
        "imports none, so the only card images this product shows are the "
        "user's own uploads. No prices: `GET /cards/{id}/market` is M4."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": (
                "No card is recorded under that identifier. `card_not_identified` "
                "from the spec §66 taxonomy; `details.card_id` says which."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The card catalog could not be reached.",
        },
    },
)
async def read_card(
    repository: Annotated[CardRepository, Depends(card_repository)],
    card_id: Annotated[
        UUID,
        Path(description="This catalog's identifier for the card, as returned by a search."),
    ],
) -> CardResponse:
    """Return one card, or say why there is none to return.

    `card_id` is typed `UUID`, so a malformed identifier is FastAPI's own 422
    rather than an `ErrorResponse`. That is the existing decision, not a new
    one: `tcg_api.errors` deliberately leaves request-validation responses alone
    because a malformed path segment is a transport-level failure with no §66
    meaning, and forcing one into a taxonomy code would be a lie in the single
    field callers are meant to trust. A *well-formed* identifier naming no card
    is a different question, and that one gets the envelope.
    """
    identifier = CardId(card_id)
    try:
        card = await repository.get(identifier)
        # Read through the same repository, and therefore the same session, so
        # the two answers cannot describe different states of the catalog. Only
        # asked for when there is a card to attach them to: `external_ids`
        # answers empty for an unknown card as well as for a card no source has
        # claimed, so on the 404 path it is a round trip that tells us nothing.
        external_ids: Sequence[CardExternalId] = (
            () if card is None else await repository.external_ids(identifier)
        )
    except CatalogUnavailable as error:
        logger.warning("card could not be read", exc_info=True)
        raise ApiError(
            ErrorCode.PROVIDER_ERROR,
            _UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"reason": "catalog_unreachable"},
        ) from error

    if card is None:
        raise ApiError(
            ErrorCode.CARD_NOT_IDENTIFIED,
            _UNKNOWN_CARD,
            status_code=status.HTTP_404_NOT_FOUND,
            details={"card_id": str(card_id)},
        )

    return _response(card, external_ids)
