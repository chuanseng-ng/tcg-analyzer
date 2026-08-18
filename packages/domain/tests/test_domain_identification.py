"""`CardIdentification` — spec §20's output, with confidence made unavoidable."""

from __future__ import annotations

from dataclasses import MISSING, FrozenInstanceError
from uuid import uuid4

import pytest
from tcg_domain import (
    INSUFFICIENT_INFORMATION,
    Card,
    CardId,
    CardIdentification,
    Confidence,
    Game,
    InvalidCardIdentification,
    Language,
    Set,
    SetId,
    Uncertain,
)


def a_card() -> Card:
    return Card(
        id=CardId(uuid4()),
        set=Set(
            id=SetId(uuid4()),
            game=Game.POKEMON,
            language=Language.ENGLISH,
            set_code="base1",
            name="Base Set",
        ),
        card_number="4",
        name="Charizard",
        rarity="Rare Holo",
        variant="1st-edition-holo",
    )


def test_an_identification_carries_a_candidate_and_its_confidence() -> None:
    identification = CardIdentification(card=a_card(), confidence=Confidence.of(0.82))
    assert identification.card.name == "Charizard"
    assert identification.confidence == Confidence.of(0.82)


def test_it_is_frozen() -> None:
    identification = CardIdentification(card=a_card(), confidence=Confidence.of(0.82))
    with pytest.raises(FrozenInstanceError):
        identification.confidence = Confidence.of(1.0)  # type: ignore[misc]


# --------------------------------------------------------------------------
# Confidence is required. Spec §20: "Never silently use an uncertain card
# identification for economic analysis." An optional field makes that easy to do.
# --------------------------------------------------------------------------


def test_an_identification_cannot_be_built_without_a_confidence() -> None:
    with pytest.raises(TypeError):
        CardIdentification(card=a_card())  # type: ignore[call-arg]


def test_confidence_has_no_default_to_fall_back_on() -> None:
    declared = CardIdentification.__dataclass_fields__["confidence"]
    assert declared.default is MISSING
    assert declared.default_factory is MISSING


@pytest.mark.parametrize("value", [0.82, 82, "0.82", None])
def test_a_bare_number_is_not_a_confidence(value: object) -> None:
    """`Confidence` is what makes `[0, 1]` true; a float would smuggle 82 in."""
    with pytest.raises(InvalidCardIdentification):
        CardIdentification(card=a_card(), confidence=value)  # type: ignore[arg-type]


def test_the_candidate_must_be_a_card() -> None:
    with pytest.raises(InvalidCardIdentification):
        CardIdentification(card="Charizard", confidence=Confidence.of(0.82))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Saying "I could not identify it" is a result, not an exception
# --------------------------------------------------------------------------


def test_an_identifier_may_answer_that_it_does_not_know() -> None:
    """`Uncertain[CardIdentification]` is the identifier's natural return type."""
    unknown: Uncertain[CardIdentification] = INSUFFICIENT_INFORMATION
    assert not unknown


def test_it_reads_as_the_printed_card_and_a_percentage() -> None:
    identification = CardIdentification(card=a_card(), confidence=Confidence.of(0.82))
    assert str(identification) == "pokemon/en/base1-4 (1st-edition-holo) — 82%"
