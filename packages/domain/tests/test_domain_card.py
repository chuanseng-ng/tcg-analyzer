"""`CardReference` â€” TCG-agnostic identity of a single printed card."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from tcg_domain import ENGLISH, JAPANESE, POKEMON, CardReference, InvalidCardReference


def a_card(**overrides: object) -> CardReference:
    fields: dict[str, object] = {
        "game": POKEMON,
        "language": ENGLISH,
        "set_code": "base1",
        "card_number": "4",
        "variant": None,
    }
    fields.update(overrides)
    return CardReference(**fields)  # type: ignore[arg-type]


def test_a_complete_reference() -> None:
    card = a_card(variant="1st-edition-holo")
    assert card.game == "pokemon"
    assert card.language == "en"
    assert card.set_code == "base1"
    assert card.card_number == "4"
    assert card.variant == "1st-edition-holo"


def test_variant_is_optional() -> None:
    assert a_card().variant is None


def test_reference_is_frozen() -> None:
    card = a_card()
    with pytest.raises(FrozenInstanceError):
        card.game = "magic"  # type: ignore[misc]


def test_reference_is_hashable() -> None:
    assert len({a_card(), a_card(), a_card(card_number="5")}) == 2


# --------------------------------------------------------------------------
# `game` is a field, not an enum. V1 ships PokÃ©mon; nothing hard-codes it.
# --------------------------------------------------------------------------


def test_pokemon_is_a_convenience_constant_not_a_restriction() -> None:
    assert POKEMON == "pokemon"


def test_another_game_is_representable() -> None:
    """The domain is TCG-agnostic; a future game must need no code change."""
    assert a_card(game="magic-the-gathering").game == "magic-the-gathering"


@pytest.mark.parametrize(
    "game", ["", "   ", "Pokemon", "pokÃ© mon", "pokemon_tcg", "-pokemon", "pokemon-"]
)
def test_game_must_be_a_lowercase_slug(game: str) -> None:
    with pytest.raises(InvalidCardReference):
        a_card(game=game)


def test_game_must_be_a_string() -> None:
    with pytest.raises(InvalidCardReference):
        a_card(game=None)


# --------------------------------------------------------------------------
# `language` is ISO 639-1
# --------------------------------------------------------------------------


def test_v1_languages() -> None:
    assert ENGLISH == "en"
    assert JAPANESE == "ja"
    assert a_card(language=JAPANESE).language == "ja"


@pytest.mark.parametrize("language", ["", "EN", "eng", "e", "en-US", "12"])
def test_language_must_be_an_iso_639_1_code(language: str) -> None:
    with pytest.raises(InvalidCardReference):
        a_card(language=language)


# --------------------------------------------------------------------------
# Remaining fields
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["set_code", "card_number"])
@pytest.mark.parametrize("value", ["", "   ", " base1", "base1 "])
def test_identifying_fields_must_be_non_empty_and_untrimmed(field: str, value: str) -> None:
    with pytest.raises(InvalidCardReference):
        a_card(**{field: value})


@pytest.mark.parametrize("field", ["set_code", "card_number"])
def test_identifying_fields_must_be_strings(field: str) -> None:
    with pytest.raises(InvalidCardReference):
        a_card(**{field: 4})


def test_set_codes_are_not_forced_lowercase() -> None:
    """Set codes are printed identifiers; the domain records them verbatim."""
    assert a_card(set_code="SV3a").set_code == "SV3a"


def test_card_numbers_keep_their_printed_form() -> None:
    assert a_card(card_number="102/102").card_number == "102/102"


@pytest.mark.parametrize("variant", ["", "   ", " holo"])
def test_variant_must_be_absent_or_meaningful(variant: str) -> None:
    with pytest.raises(InvalidCardReference):
        a_card(variant=variant)
