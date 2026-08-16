"""A reference to one printed card, independent of any card database.

The card domain is TCG-agnostic. V1 ships Pokémon only, but `game` is a
**validated field, not an enum**: hard-coding the game here would put "Pokémon"
into the type system, and every later TCG would then be a schema change rather
than a row of data. :data:`POKEMON` exists for convenience, not as a
restriction.

The identity is deliberately the printed one — game, language, set, number,
variant — rather than any provider's identifier, so no card database can become
a hard dependency of the core domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tcg_domain.errors import InvalidCardReference

__all__ = ["ENGLISH", "JAPANESE", "POKEMON", "CardReference"]

#: The only game V1 ships. A convenience constant, not the set of valid games.
POKEMON: Final = "pokemon"

#: The two languages V1 ships (spec: Pokémon, English and Japanese).
ENGLISH: Final = "en"
JAPANESE: Final = "ja"

_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ISO_639_1_PATTERN: Final = re.compile(r"^[a-z]{2}$")


def _validated_slug(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise InvalidCardReference(f"{field} must be a string, got {type(value).__name__}")
    if not _SLUG_PATTERN.match(value):
        raise InvalidCardReference(
            f"{field} must be a lowercase slug such as 'pokemon', got {value!r}"
        )
    return value


def _validated_identifier(field: str, value: object) -> str:
    """Accept a printed identifier verbatim, rejecting only empty or padded ones.

    Set codes and card numbers are printed on the card in whatever form the
    publisher chose — ``SV3a``, ``102/102``. Normalising them would destroy
    information the identification step depends on.
    """
    if not isinstance(value, str):
        raise InvalidCardReference(f"{field} must be a string, got {type(value).__name__}")
    if not value:
        raise InvalidCardReference(f"{field} must not be empty")
    if value != value.strip():
        raise InvalidCardReference(f"{field} must not be padded with whitespace: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class CardReference:
    """The identity of a single printed card.

    Args:
        game: A lowercase slug — :data:`POKEMON` in V1.
        language: An ISO 639-1 code — :data:`ENGLISH` or :data:`JAPANESE` in V1.
        set_code: The publisher's set identifier, recorded verbatim.
        card_number: The number printed on the card, recorded verbatim.
        variant: A printing variant (``"1st-edition-holo"``), or None when the
            card has no variant.

    Raises:
        InvalidCardReference: If any field is empty or malformed.
    """

    game: str
    language: str
    set_code: str
    card_number: str
    variant: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "game", _validated_slug("game", self.game))

        if not isinstance(self.language, str) or not _ISO_639_1_PATTERN.match(self.language):
            raise InvalidCardReference(
                f"language must be a lowercase ISO 639-1 code such as 'en', got {self.language!r}"
            )

        object.__setattr__(self, "set_code", _validated_identifier("set_code", self.set_code))
        object.__setattr__(
            self, "card_number", _validated_identifier("card_number", self.card_number)
        )
        if self.variant is not None:
            object.__setattr__(self, "variant", _validated_identifier("variant", self.variant))

    def __str__(self) -> str:
        printed = f"{self.game}/{self.language}/{self.set_code}-{self.card_number}"
        return printed if self.variant is None else f"{printed} ({self.variant})"
