"""A reference to one printed card, independent of any card database.

The card domain is TCG-agnostic. V1 ships Pokémon only, but `game` is a
**validated field, not an enum**: hard-coding the game here would put "Pokémon"
into the type system, and every later TCG would then be a schema change rather
than a row of data. :class:`Game` and :class:`Language` exist for convenience,
not as a restriction — their members are `str`, so they may be passed anywhere
a game or a language is wanted, and a value neither of them names is still
accepted.

The identity is deliberately the printed one — game, language, set, number,
variant — rather than any provider's identifier, so no card database can become
a hard dependency of the core domain.

The three validators below are shared with :mod:`tcg_domain.catalog` and
:mod:`tcg_domain.repository`, so a set code means the same thing wherever it
appears. Each takes the exception it should raise, because "this is not a card
number" is a different report depending on what was being built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from tcg_domain.errors import DomainError, InvalidCardReference

__all__ = ["ENGLISH", "JAPANESE", "POKEMON", "CardReference", "Game", "Language"]


class Game(StrEnum):
    """The games V1 knows about — a vocabulary, not the set of valid games.

    Members are `str`, so ``Game.POKEMON`` is accepted by every field typed
    `str` and compares equal to ``"pokemon"``. Adding a TCG stays a row of
    data: a game absent from this enum is still representable.
    """

    POKEMON = "pokemon"


class Language(StrEnum):
    """The languages V1 ships, as ISO 639-1 codes — again a vocabulary.

    Japanese sets are not translations of English sets; they are different sets
    with their own numbering (see
    ``docs/adr/0004-the-canonical-card-catalog-source.md``), which is why the
    language belongs to the identity rather than to a rendering layer.
    """

    ENGLISH = "en"
    JAPANESE = "ja"


#: The only game V1 ships. A convenience constant, not the set of valid games.
POKEMON: Final = Game.POKEMON

#: The two languages V1 ships (spec: Pokémon, English and Japanese).
ENGLISH: Final = Language.ENGLISH
JAPANESE: Final = Language.JAPANESE

_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ISO_639_1_PATTERN: Final = re.compile(r"^[a-z]{2}$")


def validated_slug(field: str, value: object, *, error: type[DomainError]) -> str:
    """Accept a lowercase slug — a game key, a provider key.

    The result is coerced to a plain `str`, so a :class:`Game` member does not
    survive into a repr, a log line or a serialised payload as ``Game.POKEMON``.
    """
    if not isinstance(value, str):
        raise error(f"{field} must be a string, got {type(value).__name__}")
    if not _SLUG_PATTERN.match(value):
        raise error(f"{field} must be a lowercase slug such as 'pokemon', got {value!r}")
    return str(value)


def validated_language(value: object, *, error: type[DomainError]) -> str:
    """Accept an ISO 639-1 code — ``en``, ``ja``, and whatever V2 adds."""
    if not isinstance(value, str) or not _ISO_639_1_PATTERN.match(value):
        raise error(f"language must be a lowercase ISO 639-1 code such as 'en', got {value!r}")
    return str(value)


def validated_identifier(field: str, value: object, *, error: type[DomainError]) -> str:
    """Accept printed text verbatim, rejecting only empty or padded values.

    Set codes, card numbers and names are printed on the card in whatever form
    the publisher chose — ``SV3a``, ``102/102``, ``リザードン``. Normalising them
    would destroy information the identification step depends on, and casing a
    Japanese name is meaningless.
    """
    if not isinstance(value, str):
        raise error(f"{field} must be a string, got {type(value).__name__}")
    if not value:
        raise error(f"{field} must not be empty")
    if value != value.strip():
        raise error(f"{field} must not be padded with whitespace: {value!r}")
    return str(value)


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
        object.__setattr__(
            self, "game", validated_slug("game", self.game, error=InvalidCardReference)
        )
        object.__setattr__(
            self, "language", validated_language(self.language, error=InvalidCardReference)
        )
        object.__setattr__(
            self,
            "set_code",
            validated_identifier("set_code", self.set_code, error=InvalidCardReference),
        )
        object.__setattr__(
            self,
            "card_number",
            validated_identifier("card_number", self.card_number, error=InvalidCardReference),
        )
        if self.variant is not None:
            object.__setattr__(
                self,
                "variant",
                validated_identifier("variant", self.variant, error=InvalidCardReference),
            )

    def __str__(self) -> str:
        printed = f"{self.game}/{self.language}/{self.set_code}-{self.card_number}"
        return printed if self.variant is None else f"{printed} ({self.variant})"
