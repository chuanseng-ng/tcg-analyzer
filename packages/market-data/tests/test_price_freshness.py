"""What an old price is worth — spec §38's `price_age` and `price_confidence`.

Two rules the fixtures below exist to pin down. **Age is asked, never stored**:
every number here is a function of `observed_at` and the moment of asking, so a
column holding one would be wrong the second after it was written. And **stale
data may be used, as long as it is labelled**: the decay bottoms out at
:data:`STALE_FLOOR` rather than at zero, because an observation worth nothing is
indistinguishable from no observation, and §38 forbids only *silent*
substitution.

The arithmetic is hand-calculated rather than asserted against the
implementation's own formula, which is this repository's rule for anything a
money figure passes through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tcg_domain import ENGLISH, POKEMON, CardReference, Confidence, Money
from tcg_market_data import InvalidMarketObservation, PriceObservation
from tcg_market_data.freshness import (
    FRESH_WITHIN,
    STALE_FLOOR,
    price_age,
    price_confidence,
)

CHARIZARD = CardReference(
    game=POKEMON,
    language=ENGLISH,
    set_code="base1",
    card_number="4/102",
    variant="unlimited-holo",
)
OBSERVED_AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
SURE = Confidence.of(0.9)

#: Seven days — the API's default, and six missed daily runs.
A_WEEK = timedelta(days=7)


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


# ---------------------------------------------------------------------------
# price_age
# ---------------------------------------------------------------------------
def test_age_is_the_distance_from_the_observation_to_the_moment_of_asking() -> None:
    assert price_age(an_observation(), at=OBSERVED_AT + timedelta(hours=26)) == timedelta(hours=26)


def test_asking_at_the_instant_it_was_observed_is_no_age_at_all() -> None:
    assert price_age(an_observation(), at=OBSERVED_AT) == timedelta(0)


def test_a_price_observed_in_the_future_clamps_to_zero_rather_than_going_negative() -> None:
    """A provider's clock running ahead is ordinary; a negative age is not.

    Clamping keeps `price_confidence` monotonic — the alternative is an
    observation scoring *above* the provider's own confidence for being
    fashionably early.
    """
    ahead = an_observation(observed_at=OBSERVED_AT + timedelta(hours=3))
    assert price_age(ahead, at=OBSERVED_AT) == timedelta(0)


def test_a_naive_moment_of_asking_is_refused() -> None:
    """The same rule `PriceObservation` applies to `observed_at`.

    A naive `at` compared against an aware `observed_at` raises `TypeError`
    deep in the stdlib; refusing here says which argument was wrong.
    """
    with pytest.raises(InvalidMarketObservation) as raised:
        price_age(an_observation(), at=datetime(2026, 8, 25, 9, 0))  # noqa: DTZ001
    assert "at" in str(raised.value)


# ---------------------------------------------------------------------------
# price_confidence — hand-calculated, with FRESH_WITHIN=1d, STALE_FLOOR=0.05
# ---------------------------------------------------------------------------
def test_a_fresh_price_is_worth_exactly_what_the_provider_said_it_was() -> None:
    """Nothing has decayed yet, so nothing is subtracted."""
    assert price_confidence(an_observation(), at=OBSERVED_AT, stale_after=A_WEEK) == SURE


def test_confidence_is_still_undiminished_at_the_end_of_one_ingestion_cycle() -> None:
    """§37 refreshes daily, so a price up to a cycle old is simply current."""
    at = OBSERVED_AT + FRESH_WITHIN
    assert price_confidence(an_observation(), at=at, stale_after=A_WEEK) == SURE


def test_confidence_decays_linearly_between_the_fresh_window_and_the_threshold() -> None:
    """Four days old: three of the six decaying days spent, so half the fall.

    factor = 1 - (1 - 0.05) x (3 / 6) = 0.525, and 0.9 x 0.525 = 0.4725.
    """
    at = OBSERVED_AT + timedelta(days=4)
    result = price_confidence(an_observation(), at=at, stale_after=A_WEEK)
    assert result.value == pytest.approx(0.4725)


def test_confidence_reaches_the_floor_at_the_staleness_threshold() -> None:
    """0.9 x 0.05 = 0.045."""
    at = OBSERVED_AT + A_WEEK
    result = price_confidence(an_observation(), at=at, stale_after=A_WEEK)
    assert result.value == pytest.approx(0.045)


def test_a_very_old_price_never_falls_below_the_floor() -> None:
    """Ten times past the threshold is worth the same as one step past it.

    The floor is what makes "stale but labelled" a usable answer rather than a
    silent one — §38 forbids substituting stale data *without identifying it*,
    not using it.
    """
    at = OBSERVED_AT + 10 * A_WEEK
    result = price_confidence(an_observation(), at=at, stale_after=A_WEEK)
    assert result.value == pytest.approx(0.045)


def test_the_decay_is_applied_to_the_providers_own_confidence_not_in_place_of_it() -> None:
    """A weak observation stays weak. Staleness multiplies; it does not replace."""
    weak = an_observation(confidence=Confidence.of(0.4))
    at = OBSERVED_AT + A_WEEK
    assert price_confidence(weak, at=at, stale_after=A_WEEK).value == pytest.approx(0.02)


def test_a_price_observed_in_the_future_is_not_rewarded_for_it() -> None:
    ahead = an_observation(observed_at=OBSERVED_AT + timedelta(days=3))
    assert price_confidence(ahead, at=OBSERVED_AT, stale_after=A_WEEK) == SURE


def test_a_zero_price_still_carries_a_real_confidence() -> None:
    """The case the whole port exists for: worth nothing is not the same as unknown.

    A worthless card observed this morning is a confident measurement, and
    nothing about the *amount* may leak into how much the observation is worth.
    """
    worthless = an_observation(price=Money.zero())
    assert price_confidence(worthless, at=OBSERVED_AT, stale_after=A_WEEK) == SURE


def test_a_naive_moment_of_asking_is_refused_here_too() -> None:
    with pytest.raises(InvalidMarketObservation):
        price_confidence(
            an_observation(),
            at=datetime(2026, 8, 25, 9, 0),  # noqa: DTZ001
            stale_after=A_WEEK,
        )


def test_a_threshold_inside_the_fresh_window_is_a_misconfiguration_and_says_so() -> None:
    """There is no curve to draw between two points in the wrong order.

    Refused rather than clamped: a deployment that set this by accident would
    otherwise report every price beyond a day at the floor and look like a
    provider outage.
    """
    with pytest.raises(ValueError, match="stale_after"):
        price_confidence(an_observation(), at=OBSERVED_AT, stale_after=FRESH_WITHIN)


def test_the_floor_is_above_zero() -> None:
    """Guards the constant itself, not the arithmetic that reads it."""
    assert 0.0 < STALE_FLOOR < 1.0
