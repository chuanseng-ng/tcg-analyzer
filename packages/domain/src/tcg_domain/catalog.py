"""The canonical card catalog — spec §10's three tables, as domain entities.

Spec §10 opens with the constraint that governs this module: "the canonical
card database must not be tied to a particular external provider". So nothing
here names one. TCGdex is the V1 source
(``docs/adr/0004-the-canonical-card-catalog-source.md``) and it enters as a row
in :class:`CardExternalId` under the provider key ``tcgdex``, alongside the
hand-authored ``manual`` fixtures — never as a column, a type or an import.

Three deliberate departures from the §10 column lists, each recorded so the
schema work in #23 knows they are choices rather than omissions:

* **A card holds its set, not a set id.** Every screen that shows a card shows
  its set — a card number without one identifies nothing — so the two are
  always loaded together, and :attr:`Card.reference` can then produce the
  printed identity :class:`~tcg_domain.card.CardReference` already models
  instead of repeating five fields.
* **No `image_front` or `image_back`.** ADR 0004 keeps them `NULL` for V1: MIT
  covers TCGdex's compilation, not The Pokémon Company's artwork, and the only
  card images the product shows are the user's own uploads. A field that can
  only ever be empty is an invitation to render an empty image.
* **No `created_at` or `updated_at`, and no surrogate id on an external id.**
  Row bookkeeping belongs to the table, and nothing in the domain references an
  external id — its identity is ``(card_id, provider, external_id)``.

Text is recorded verbatim. Set codes, card numbers and names are printed on the
card in whatever form the publisher chose, and V1 ships Japanese, where casing
and punctuation normalisation are meaningless at best.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, NewType
from uuid import UUID

from tcg_domain.card import (
    CardReference,
    validated_identifier,
    validated_language,
    validated_slug,
)
from tcg_domain.errors import InvalidCatalogRecord

__all__ = ["Card", "CardExternalId", "CardId", "Set", "SetId"]

#: A set's canonical identifier. Ours, not a provider's — a provider's is a
#: :class:`CardExternalId`. Distinct from :data:`CardId` so that passing one
#: where the other is wanted is a type error rather than a silent miss.
SetId = NewType("SetId", UUID)

#: A card's canonical identifier, on the same terms.
CardId = NewType("CardId", UUID)

_NO_METADATA: Final[Mapping[str, object]] = MappingProxyType({})


def _validated_uuid(field: str, value: object) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidCatalogRecord(f"{field} must be a UUID, got {type(value).__name__}")
    return value


def _validated_release_date(value: object) -> date:
    # `datetime` is a subclass of `date`, so it would pass the isinstance check
    # below and smuggle a time and a timezone into a field that means "the day
    # this set went on sale". Rejecting it explicitly is the only way to keep
    # that field honest.
    if isinstance(value, datetime):
        raise InvalidCatalogRecord(
            f"release_date must be a calendar date, not a timestamp, got {value!r}"
        )
    if not isinstance(value, date):
        raise InvalidCatalogRecord(f"release_date must be a date, got {type(value).__name__}")
    return value


def _validated_metadata(value: object) -> Mapping[str, object]:
    """Copy the caller's mapping behind a read-only view.

    Copied so a record cannot be changed after it has been validated, and
    read-only so the copy is not quietly defeated by mutating it in place —
    the same pair of guarantees :class:`~tcg_domain.distribution.GradeDistribution`
    makes. The cost is that catalog records are not hashable; nothing needs
    them to be.
    """
    if not isinstance(value, Mapping):
        raise InvalidCatalogRecord(f"metadata must be a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise InvalidCatalogRecord(f"metadata keys must be strings, got {type(key).__name__}")
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Set:
    """One published set — the group a card number is printed relative to.

    Args:
        id: Our identifier for this set.
        game: A lowercase slug — :data:`~tcg_domain.card.POKEMON` in V1.
        language: An ISO 639-1 code. Japanese sets are distinct sets, not
            translations, so this is part of the set's identity.
        set_code: The publisher's set identifier, recorded verbatim — ``SV3a``.
        name: The set's printed name, in its own language.
        release_date: The day the set went on sale, where it is known.
        metadata: Anything the source carried that has no column of its own.
            Copied on construction.

    Raises:
        InvalidCatalogRecord: If any field is empty or malformed.
    """

    id: SetId
    game: str
    language: str
    set_code: str
    name: str
    release_date: date | None = None
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "id", _validated_uuid("id", self.id))
        set_field(self, "game", validated_slug("game", self.game, error=InvalidCatalogRecord))
        set_field(self, "language", validated_language(self.language, error=InvalidCatalogRecord))
        set_field(
            self,
            "set_code",
            validated_identifier("set_code", self.set_code, error=InvalidCatalogRecord),
        )
        set_field(self, "name", validated_identifier("name", self.name, error=InvalidCatalogRecord))
        if self.release_date is not None:
            set_field(self, "release_date", _validated_release_date(self.release_date))
        set_field(self, "metadata", _validated_metadata(self.metadata))

    def __str__(self) -> str:
        return f"{self.set_code} ({self.language})"


@dataclass(frozen=True, slots=True)
class Card:
    """One canonical card in the catalog.

    Args:
        id: Our identifier for this card.
        set: The set it was printed in. It carries the game and the language,
            which is why the card does not repeat them: a card cannot disagree
            with its own set.
        card_number: The number printed on the card, verbatim — ``102/102``.
        name: The card's printed name, in its own language.
        rarity: The printed rarity, where the source records one.
        variant: The printing variant — ``"1st-edition-holo"``. Holo, reverse
            holo and 1st edition are economically different cards, so this is
            never inferred and never dropped.
        metadata: Anything the source carried that has no column of its own.
            Copied on construction.

    Raises:
        InvalidCatalogRecord: If any field is empty or malformed.
    """

    id: CardId
    set: Set
    card_number: str
    name: str
    rarity: str | None = None
    variant: str | None = None
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "id", _validated_uuid("id", self.id))
        if not isinstance(self.set, Set):
            raise InvalidCatalogRecord(
                f"set must be a Set, not a set id, got {type(self.set).__name__}"
            )
        set_field(
            self,
            "card_number",
            validated_identifier("card_number", self.card_number, error=InvalidCatalogRecord),
        )
        set_field(self, "name", validated_identifier("name", self.name, error=InvalidCatalogRecord))
        for optional in ("rarity", "variant"):
            value = getattr(self, optional)
            if value is not None:
                set_field(
                    self,
                    optional,
                    validated_identifier(optional, value, error=InvalidCatalogRecord),
                )
        set_field(self, "metadata", _validated_metadata(self.metadata))

    @property
    def game(self) -> str:
        """The game, read through the set."""
        return self.set.game

    @property
    def language(self) -> str:
        """The language, read through the set."""
        return self.set.language

    @property
    def reference(self) -> CardReference:
        """This card's printed identity, owing nothing to any card database.

        The bridge between the catalog and the rest of the pipeline: analysis,
        market lookup and the economic engine all speak
        :class:`~tcg_domain.card.CardReference`, so none of them acquires a
        dependency on the catalog having been imported.
        """
        return CardReference(
            game=self.set.game,
            language=self.set.language,
            set_code=self.set.set_code,
            card_number=self.card_number,
            variant=self.variant,
        )

    def __str__(self) -> str:
        return f"{self.name} — {self.reference}"


@dataclass(frozen=True, slots=True)
class CardExternalId:
    """One external database's identifier for a canonical card.

    Spec §10: "this allows multiple external databases to reference the same
    canonical card". It is also the seam that keeps them replaceable — a source
    that has to be dropped costs a delete from this table and nothing else.

    Args:
        card_id: The canonical card this identifier refers to.
        provider: A lowercase slug naming the source — ``tcgdex`` or ``manual``
            in V1 (ADR 0004).
        external_id: The identifier as that provider issued it, verbatim.
        metadata: Anything the source carried that has no column of its own.
            Copied on construction.

    Raises:
        InvalidCatalogRecord: If any field is empty or malformed.
    """

    card_id: CardId
    provider: str
    external_id: str
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "card_id", _validated_uuid("card_id", self.card_id))
        set_field(
            self,
            "provider",
            validated_slug("provider", self.provider, error=InvalidCatalogRecord),
        )
        set_field(
            self,
            "external_id",
            validated_identifier("external_id", self.external_id, error=InvalidCatalogRecord),
        )
        set_field(self, "metadata", _validated_metadata(self.metadata))

    def __str__(self) -> str:
        return f"{self.provider}:{self.external_id}"
