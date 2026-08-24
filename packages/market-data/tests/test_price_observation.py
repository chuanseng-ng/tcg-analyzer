"""What a price observation refuses to be.

The two rules worth stating up front, because everything below serves them:
**zero is a price** and a half-graded record is not a record. The first is the
distinction spec §38 rests on; the second is spec §35's constraint, which #50
also writes in SQL — neither is a substitute for the other, because #51 and #52
construct these objects long before anything reaches a database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tcg_domain import ENGLISH, POKEMON, CardReference, Confidence, Grade, Money
from tcg_grading_companies import UnsupportedGrade
from tcg_market_data import InvalidMarketObservation, MarketType, PriceObservation

CHARIZARD = CardReference(
    game=POKEMON,
    language=ENGLISH,
    set_code="base1",
    card_number="4/102",
    variant="unlimited-holo",
)
OBSERVED_AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
SURE = Confidence.of(0.9)


def an_observation(**overrides: object) -> PriceObservation:
    fields: dict[str, object] = {
        "card": CHARIZARD,
        "price": Money.of("420.00"),
        "observed_at": OBSERVED_AT,
        "confidence": SURE,
        "provider": "memory",
    }
    fields.update(overrides)
    return PriceObservation(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Zero is a price
# --------------------------------------------------------------------------
def test_a_zero_price_is_a_perfectly_ordinary_observation() -> None:
    """A worthless card is worth nothing, which is a measurement."""
    observation = an_observation(price=Money.zero())
    assert observation.price == Money.zero()
    assert bool(observation) is True


def test_a_negative_price_is_not_a_price_anybody_saw() -> None:
    with pytest.raises(InvalidMarketObservation) as raised:
        an_observation(price=Money.of("-1.00"))
    assert "negative" in str(raised.value)


def test_a_float_price_is_refused_by_money_itself() -> None:
    with pytest.raises(ValueError, match="float"):
        Money.of(12.34)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Raw and graded are the same type and cannot be half of each other
# --------------------------------------------------------------------------
def test_an_observation_with_no_company_is_raw() -> None:
    assert an_observation().market_type is MarketType.RAW


def test_an_observation_with_a_company_is_graded() -> None:
    graded = an_observation(grading_company="psa", grade=Grade.parse("10"))
    assert graded.market_type is MarketType.GRADED


@pytest.mark.parametrize(
    "overrides",
    [
        {"grading_company": "psa"},
        {"grade": Grade.parse("10")},
    ],
    ids=["company-without-grade", "grade-without-company"],
)
def test_a_half_graded_observation_is_not_representable(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidMarketObservation) as raised:
        an_observation(**overrides)
    assert "grading company" in str(raised.value)


# --------------------------------------------------------------------------
# Grade keys against the company's scale
# --------------------------------------------------------------------------
def test_a_psa_nine_and_a_half_is_refused_at_construction() -> None:
    """Not merely at query time: no implementation can store one."""
    with pytest.raises(UnsupportedGrade) as raised:
        an_observation(grading_company="psa", grade=Grade.parse("9.5"))
    assert "psa" in str(raised.value)


def test_a_bgs_nine_and_a_half_is_accepted() -> None:
    """The one grade the three companies disagree about."""
    observation = an_observation(grading_company="bgs", grade=Grade.parse("9.5"))
    assert observation.grade == Grade.parse("9.5")


def test_a_psa_eight_and_a_half_is_accepted() -> None:
    """All three issue half grades; 9.5 is the exception, not the rule."""
    assert an_observation(grading_company="psa", grade=Grade.parse("8.5")).grade is not None


def test_a_company_with_no_adapter_is_accepted_rather_than_refused() -> None:
    """`GradingCompany` is a vocabulary — see validated_grade_key's comment."""
    observation = an_observation(grading_company="cgc", grade=Grade.parse("9.5"))
    assert observation.grading_company == "cgc"


# --------------------------------------------------------------------------
# Provenance and timestamps
# --------------------------------------------------------------------------
def test_the_provider_is_a_slug_not_a_display_name() -> None:
    """ADR 0006's `PokePriceTracker` belongs in #50's market_providers.name."""
    with pytest.raises(InvalidMarketObservation) as raised:
        an_observation(provider="PokePriceTracker")
    assert "slug" in str(raised.value)


def test_a_naive_timestamp_is_refused() -> None:
    """A naive observed_at makes #55's price_age silently wrong."""
    with pytest.raises(InvalidMarketObservation) as raised:
        an_observation(observed_at=datetime(2026, 8, 24, 9, 0))  # noqa: DTZ001
    assert "timezone-aware" in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", Decimal("1.00")),
        ("confidence", 0.9),
        ("card", "base1-4"),
    ],
    ids=["price", "confidence", "card"],
)
def test_a_field_of_the_wrong_type_is_refused(field: str, value: object) -> None:
    with pytest.raises(InvalidMarketObservation):
        an_observation(**{field: value})


def test_an_observation_is_frozen() -> None:
    observation = an_observation()
    with pytest.raises(AttributeError):
        observation.price = Money.zero()  # type: ignore[misc]


def test_a_grade_that_is_a_string_is_refused() -> None:
    """Provider responses carry string keys; `Grade.parse` is the one boundary."""
    with pytest.raises(InvalidMarketObservation) as raised:
        an_observation(grading_company="psa", grade="10")
    assert "Grade.parse" in str(raised.value)
