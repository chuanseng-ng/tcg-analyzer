"""The card catalog's per-card HTTP surface — spec §64's `GET /cards/{id}`.

One endpoint today: the full canonical detail for one card, so a user can
confirm it is the card in their hand before any analysis proceeds. Spec §6's
Card block — name, set, card number, language, variant — is what this answers,
minus identification confidence, which belongs to an analysis rather than to a
catalog record.

**No card images.** `docs/adr/0004-the-canonical-card-catalog-source.md` imports
none: TCGdex's MIT licence covers its compilation, not The Pokémon Company's
artwork. `cards.image_front` and `cards.image_back` are therefore always NULL,
and the response omits them rather than returning empty URLs — a field that can
only ever be empty is an invitation to render an empty image. The only card
images this product shows are the user's own uploads (M2).

The router holds HTTP and nothing else; the SQL lives in `tcg_api.catalog.cards`,
behind the domain's port, exactly as `routers/catalog.py` delegates to
`tcg_api.catalog.versions`.

**#28 adds `GET /cards/search` to this module, and must declare it above
`read_card`.** FastAPI matches routes in declaration order, so a literal path
registered after `/{card_id}` is shadowed by it — and because `card_id` is typed
`UUID`, the symptom would be a 422 complaining that "search" is not a valid
UUID rather than an obviously missing route.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from tcg_domain.catalog import Card, CardExternalId, CardId
from tcg_domain.errors import CatalogUnavailable
from tcg_domain.repository import CardRepository

from tcg_api.catalog.cards import PostgresCardRepository
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse

__all__ = [
    "CardExternalIdResponse",
    "CardResponse",
    "CardSetResponse",
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
