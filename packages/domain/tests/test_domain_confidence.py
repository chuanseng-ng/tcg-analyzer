"""`Confidence` and `InsufficientInformation` — spec §2.7.

Uncertainty is a product feature. "We cannot assess this" must be expressible
as a *result*, not signalled by an exception.
"""

from __future__ import annotations

import math

import pytest

from tcg_domain import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    DomainError,
    InsufficientInformation,
    InvalidConfidence,
    Uncertain,
)

# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_accepts_the_closed_unit_interval(value: float) -> None:
    assert Confidence.of(value).value == value


@pytest.mark.parametrize("value", [-0.1, 1.1, -1.0, 2.0])
def test_rejects_values_outside_the_unit_interval(value: float) -> None:
    with pytest.raises(InvalidConfidence):
        Confidence.of(value)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(InvalidConfidence):
        Confidence.of(value)


def test_rejects_a_non_number() -> None:
    with pytest.raises(InvalidConfidence):
        Confidence.of("0.5")  # type: ignore[arg-type]


def test_accepts_an_int() -> None:
    assert Confidence.of(1).value == 1.0


def test_direct_construction_validates_too() -> None:
    with pytest.raises(InvalidConfidence):
        Confidence(1.1)


def test_confidence_is_frozen() -> None:
    confidence = Confidence.of(0.82)
    with pytest.raises(Exception):
        confidence.value = 0.1  # type: ignore[misc]


def test_is_below_a_threshold() -> None:
    """The identification step must not silently use a low-confidence match."""
    assert Confidence.of(0.42).is_below(0.8)
    assert not Confidence.of(0.82).is_below(0.8)


def test_is_below_is_strict_at_the_threshold() -> None:
    assert not Confidence.of(0.8).is_below(0.8)


def test_is_below_accepts_another_confidence() -> None:
    assert Confidence.of(0.42).is_below(Confidence.of(0.8))


def test_is_below_rejects_an_out_of_range_threshold() -> None:
    with pytest.raises(InvalidConfidence):
        Confidence.of(0.5).is_below(1.5)


def test_confidence_is_ordered() -> None:
    assert Confidence.of(0.1) < Confidence.of(0.9)
    assert sorted([Confidence.of(0.9), Confidence.of(0.1)]) == [
        Confidence.of(0.1),
        Confidence.of(0.9),
    ]


def test_confidence_is_hashable() -> None:
    assert len({Confidence.of(0.5), Confidence.of(0.5), Confidence.of(0.6)}) == 2


def test_str_is_a_percentage() -> None:
    assert str(Confidence.of(0.82)) == "82%"


# --------------------------------------------------------------------------
# InsufficientInformation — a result, never an exception
# --------------------------------------------------------------------------


def test_the_sentinel_carries_no_reason() -> None:
    assert INSUFFICIENT_INFORMATION.reason is None


def test_a_reason_may_be_given() -> None:
    outcome = InsufficientInformation("image too blurred to assess surface")
    assert outcome.reason == "image too blurred to assess surface"


def test_equal_reasons_compare_equal() -> None:
    assert InsufficientInformation() == INSUFFICIENT_INFORMATION
    assert InsufficientInformation("blur") != INSUFFICIENT_INFORMATION


def test_it_is_frozen() -> None:
    with pytest.raises(Exception):
        INSUFFICIENT_INFORMATION.reason = "changed"  # type: ignore[misc]


def test_it_is_not_an_exception() -> None:
    """spec §2.7 — uncertainty is an outcome, so it must not be raisable."""
    assert not isinstance(INSUFFICIENT_INFORMATION, BaseException)
    assert not issubclass(InsufficientInformation, BaseException)
    assert not issubclass(InsufficientInformation, DomainError)


def test_it_is_falsy_so_a_caller_cannot_mistake_it_for_a_value() -> None:
    assert not INSUFFICIENT_INFORMATION


def test_str_explains_itself() -> None:
    assert str(INSUFFICIENT_INFORMATION) == "insufficient_information"
    assert str(InsufficientInformation("blur")) == "insufficient_information: blur"


def test_uncertain_is_a_usable_result_type() -> None:
    def surface_confidence(assessable: bool) -> Uncertain[Confidence]:
        return Confidence.of(0.9) if assessable else INSUFFICIENT_INFORMATION

    assert surface_confidence(True) == Confidence.of(0.9)
    assert surface_confidence(False) is INSUFFICIENT_INFORMATION
    assert isinstance(surface_confidence(False), InsufficientInformation)
