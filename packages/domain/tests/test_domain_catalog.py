"""The canonical catalog entities — spec §10's `sets`, `cards`, `card_external_ids`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from tcg_domain import (
    Card,
    CardExternalId,
    CardId,
    CardReference,
    Game,
    InvalidCatalogRecord,
    Language,
    Set,
    SetId,
)


def a_set(**overrides: object) -> Set:
    fields: dict[str, object] = {
        "id": SetId(uuid4()),
        "game": Game.POKEMON,
        "language": Language.ENGLISH,
        "set_code": "base1",
        "name": "Base Set",
        "release_date": date(1999, 1, 9),
        "metadata": {},
    }
    fields.update(overrides)
    return Set(**fields)  # type: ignore[arg-type]


def a_card(**overrides: object) -> Card:
    fields: dict[str, object] = {
        "id": CardId(uuid4()),
        "set": a_set(),
        "card_number": "4",
        "name": "Charizard",
        "rarity": "Rare Holo",
        "variant": None,
        "metadata": {},
    }
    fields.update(overrides)
    return Card(**fields)  # type: ignore[arg-type]


def an_external_id(**overrides: object) -> CardExternalId:
    fields: dict[str, object] = {
        "card_id": CardId(uuid4()),
        "provider": "tcgdex",
        "external_id": "base1-4",
        "metadata": {},
    }
    fields.update(overrides)
    return CardExternalId(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Every entity constructs without a database. That is the acceptance criterion.
# --------------------------------------------------------------------------


def test_a_complete_set() -> None:
    release = date(1999, 1, 9)
    pokemon_set = a_set(release_date=release)
    assert pokemon_set.game == "pokemon"
    assert pokemon_set.language == "en"
    assert pokemon_set.set_code == "base1"
    assert pokemon_set.name == "Base Set"
    assert pokemon_set.release_date == release


def test_a_complete_card() -> None:
    card = a_card(variant="1st-edition-holo")
    assert card.card_number == "4"
    assert card.name == "Charizard"
    assert card.rarity == "Rare Holo"
    assert card.variant == "1st-edition-holo"


def test_a_complete_external_id() -> None:
    external = an_external_id()
    assert external.provider == "tcgdex"
    assert external.external_id == "base1-4"


def test_a_set_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        a_set().name = "something else"  # type: ignore[misc]


def test_a_card_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        a_card().name = "something else"  # type: ignore[misc]


def test_an_external_id_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        an_external_id().provider = "manual"  # type: ignore[misc]


def test_optional_fields_are_optional() -> None:
    assert a_set(release_date=None).release_date is None
    plain = a_card(rarity=None, variant=None)
    assert plain.rarity is None
    assert plain.variant is None


# --------------------------------------------------------------------------
# A card reads its game and language through its set, and yields the printed
# identity `CardReference` already models.
# --------------------------------------------------------------------------


def test_a_card_reads_its_game_and_language_through_its_set() -> None:
    card = a_card(set=a_set(game="magic-the-gathering", language=Language.JAPANESE))
    assert card.game == "magic-the-gathering"
    assert card.language == "ja"


def test_a_card_yields_its_printed_reference() -> None:
    card = a_card(card_number="102/102", variant="1st-edition-holo")
    assert card.reference == CardReference(
        game="pokemon",
        language="en",
        set_code="base1",
        card_number="102/102",
        variant="1st-edition-holo",
    )


def test_a_card_without_a_variant_yields_a_reference_without_one() -> None:
    assert a_card(variant=None).reference.variant is None


# --------------------------------------------------------------------------
# `game` and `language` are still fields, not enums. The enums name what V1
# ships; they do not close the set — the card domain is TCG-agnostic.
# --------------------------------------------------------------------------


def test_the_v1_vocabulary() -> None:
    assert Game.POKEMON == "pokemon"
    assert Language.ENGLISH == "en"
    assert Language.JAPANESE == "ja"


def test_a_set_for_a_game_the_enum_does_not_name() -> None:
    assert a_set(game="magic-the-gathering").game == "magic-the-gathering"


def test_a_set_in_a_language_the_enum_does_not_name() -> None:
    assert a_set(language="fr").language == "fr"


def test_an_enum_member_is_stored_as_a_plain_string() -> None:
    """So a repr, a log line and a serialised payload all read `pokemon`."""
    stored = a_set(game=Game.POKEMON).game
    assert stored == "pokemon"
    assert type(stored) is str


@pytest.mark.parametrize("game", ["", "   ", "Pokemon", "pokemon_tcg", "-pokemon"])
def test_a_sets_game_must_be_a_lowercase_slug(game: str) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_set(game=game)


@pytest.mark.parametrize("language", ["", "EN", "eng", "en-US"])
def test_a_sets_language_must_be_an_iso_639_1_code(language: str) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_set(language=language)


# --------------------------------------------------------------------------
# Printed text is recorded verbatim; only empty and padded values are rejected.
# --------------------------------------------------------------------------


def test_printed_identifiers_keep_their_form() -> None:
    card = a_card(set=a_set(set_code="SV3a"), card_number="102/102")
    assert card.set.set_code == "SV3a"
    assert card.card_number == "102/102"


def test_a_japanese_name_survives_construction() -> None:
    card = a_card(set=a_set(language=Language.JAPANESE, name="拡張パック"), name="リザードン")
    assert card.name == "リザードン"
    assert card.set.name == "拡張パック"


@pytest.mark.parametrize("value", ["", "   ", " Base Set", "Base Set "])
@pytest.mark.parametrize("field", ["set_code", "name"])
def test_a_sets_text_must_be_present_and_untrimmed(field: str, value: str) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_set(**{field: value})


@pytest.mark.parametrize("value", ["", "   ", " 4", "4 "])
@pytest.mark.parametrize("field", ["card_number", "name", "rarity", "variant"])
def test_a_cards_text_must_be_present_and_untrimmed(field: str, value: str) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_card(**{field: value})


@pytest.mark.parametrize("value", ["", "   ", " base1-4"])
def test_an_external_id_must_be_present_and_untrimmed(value: str) -> None:
    with pytest.raises(InvalidCatalogRecord):
        an_external_id(external_id=value)


@pytest.mark.parametrize("provider", ["", "TCGdex", "tcg dex", "tcgdex-"])
def test_a_provider_key_must_be_a_lowercase_slug(provider: str) -> None:
    """`tcgdex` and `manual` are the V1 keys — ADR 0004 fixes the shape."""
    with pytest.raises(InvalidCatalogRecord):
        an_external_id(provider=provider)


def test_the_manual_provider_is_representable() -> None:
    assert an_external_id(provider="manual").provider == "manual"


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["not-a-uuid", 4, None])
def test_a_set_id_must_be_a_uuid(value: object) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_set(id=value)


@pytest.mark.parametrize("value", ["not-a-uuid", 4, None])
def test_a_card_id_must_be_a_uuid(value: object) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_card(id=value)


def test_an_external_id_points_at_a_card_by_uuid() -> None:
    with pytest.raises(InvalidCatalogRecord):
        an_external_id(card_id="base1-4")


def test_a_card_must_be_given_a_set_not_a_set_id() -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_card(set=SetId(uuid4()))


# --------------------------------------------------------------------------
# `release_date` is a calendar date
# --------------------------------------------------------------------------


def test_a_release_date_must_be_a_date() -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_set(release_date="1999-01-09")


def test_a_release_date_is_not_a_timestamp() -> None:
    """A set is released on a day, not at an instant."""
    with pytest.raises(InvalidCatalogRecord):
        a_set(release_date=datetime(1999, 1, 9, tzinfo=UTC))


# --------------------------------------------------------------------------
# `metadata` is copied, so a validated record cannot be mutated behind its back
# --------------------------------------------------------------------------


def test_metadata_defaults_to_empty() -> None:
    bare = Set(
        id=SetId(uuid4()),
        game="pokemon",
        language="en",
        set_code="base1",
        name="Base Set",
    )
    assert dict(bare.metadata) == {}


def test_metadata_is_copied_not_captured() -> None:
    source: dict[str, object] = {"series": "Base"}
    pokemon_set = a_set(metadata=source)
    source["series"] = "tampered"
    assert pokemon_set.metadata["series"] == "Base"


def test_metadata_cannot_be_mutated_through_the_record() -> None:
    card = a_card(metadata={"illustrator": "Mitsuhiro Arita"})
    with pytest.raises(TypeError):
        card.metadata["illustrator"] = "someone else"  # type: ignore[index]


@pytest.mark.parametrize("value", ["series=Base", 4, [("series", "Base")]])
def test_metadata_must_be_a_mapping(value: object) -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_card(metadata=value)


def test_metadata_keys_must_be_strings() -> None:
    with pytest.raises(InvalidCatalogRecord):
        a_card(metadata={4: "four"})


# --------------------------------------------------------------------------
# What the domain deliberately does not model
# --------------------------------------------------------------------------


def test_a_card_carries_no_catalog_images() -> None:
    """ADR 0004: MIT covers TCGdex's compilation, not the artwork. V1 shows only
    the user's own uploads, so there is no field here to leave empty."""
    card = a_card()
    assert not hasattr(card, "image_front")
    assert not hasattr(card, "image_back")


def test_a_card_carries_no_persistence_timestamps() -> None:
    card = a_card()
    assert not hasattr(card, "created_at")
    assert not hasattr(card, "updated_at")
