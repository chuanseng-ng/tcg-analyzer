"""`GradeDistribution` â€” the probability-validity invariant of spec Â§63.

An invalid distribution must be *impossible to construct*. Every test here
asserts at the constructor, never on a downstream check.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from tcg_domain import (
    SUM_TOLERANCE,
    Grade,
    GradeDistribution,
    InvalidGradeDistribution,
)

# A realistic PSA-shaped output, taken from spec Â§24.
SPEC_EXAMPLE = {"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}


# --------------------------------------------------------------------------
# spec Â§63 â€” 0 <= P(g) <= 1
# --------------------------------------------------------------------------


def test_rejects_a_negative_probability() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": -0.1, "9": 1.1})


def test_rejects_a_probability_above_one() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": 1.5, "9": -0.5})


def test_rejects_nan() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": math.nan, "9": 1.0})


def test_rejects_infinity() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": math.inf, "9": 1.0})


def test_rejects_a_non_numeric_probability() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": "0.5", "9": 0.5})  # type: ignore[dict-item]


# --------------------------------------------------------------------------
# spec Â§63 â€” sum(P(g)) ~= 1
# --------------------------------------------------------------------------


def test_rejects_a_set_summing_to_nine_tenths() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": 0.4, "9": 0.5})


def test_rejects_a_set_summing_to_one_and_a_tenth() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": 0.6, "9": 0.5})


def test_accepts_a_set_summing_to_one() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert distribution.probability_of(Grade.parse("9")) == 0.69


def test_sum_tolerance_is_documented_and_tight() -> None:
    assert SUM_TOLERANCE == 1e-6


def test_accepts_a_sum_just_inside_the_tolerance() -> None:
    """Boundary, accepted side: |sum - 1| < SUM_TOLERANCE."""
    drift = SUM_TOLERANCE / 2
    distribution = GradeDistribution.from_mapping({"10": 0.5 + drift, "9": 0.5})
    assert abs(sum(p for _, p in distribution.items()) - 1.0) < SUM_TOLERANCE


def test_rejects_a_sum_just_outside_the_tolerance() -> None:
    """Boundary, rejected side: |sum - 1| > SUM_TOLERANCE."""
    drift = SUM_TOLERANCE * 10
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"10": 0.5 + drift, "9": 0.5})


# --------------------------------------------------------------------------
# Structural rejections
# --------------------------------------------------------------------------


def test_rejects_an_empty_distribution() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({})


def test_rejects_duplicate_grade_keys() -> None:
    """`"9"` and `"9.0"` are the same grade spelled two ways."""
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"9": 0.5, "9.0": 0.5})


def test_rejects_an_unparseable_grade_key() -> None:
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"eleven": 1.0})


def test_distribution_is_frozen() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    with pytest.raises(FrozenInstanceError):
        distribution.probabilities = {}  # type: ignore[misc]


def test_the_retained_mapping_cannot_be_mutated_through() -> None:
    """The full distribution is retained (spec Â§2.1) and must stay intact."""
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    with pytest.raises(TypeError):
        distribution.probabilities[Grade.parse("9")] = 0.0  # type: ignore[index]


def test_mutating_the_source_mapping_does_not_affect_the_distribution() -> None:
    source = dict(SPEC_EXAMPLE)
    distribution = GradeDistribution.from_mapping(source)
    source["9"] = 0.0
    assert distribution.probability_of(Grade.parse("9")) == 0.69


# --------------------------------------------------------------------------
# Round-tripping the forms spec Â§24 produces
# --------------------------------------------------------------------------


def test_round_trips_the_spec_example() -> None:
    assert GradeDistribution.from_mapping(SPEC_EXAMPLE).as_mapping() == SPEC_EXAMPLE


def test_round_trips_half_grades() -> None:
    """BGS half grades â€” spec Â§24."""
    mapping = {"10": 0.25, "9.5": 0.5, "9": 0.25}
    assert GradeDistribution.from_mapping(mapping).as_mapping() == mapping


def test_round_trips_a_bucketed_tail() -> None:
    mapping = {"10": 0.3, "9": 0.6, "7_or_lower": 0.1}
    assert GradeDistribution.from_mapping(mapping).as_mapping() == mapping


def test_as_mapping_is_sorted_for_deterministic_output() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert list(distribution.as_mapping()) == ["7_or_lower", "8", "9", "10"]


def test_iteration_yields_grades_in_sorted_order() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert [str(grade) for grade in distribution] == ["7_or_lower", "8", "9", "10"]


def test_items_yields_pairs_in_sorted_order() -> None:
    distribution = GradeDistribution.from_mapping({"9": 0.4, "10": 0.6})
    assert [(str(g), p) for g, p in distribution.items()] == [("9", 0.4), ("10", 0.6)]


def test_len_counts_the_terms() -> None:
    assert len(GradeDistribution.from_mapping(SPEC_EXAMPLE)) == 4


# --------------------------------------------------------------------------
# Reading a distribution â€” never lossily
# --------------------------------------------------------------------------


def test_most_likely_grade() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert distribution.most_likely_grade == Grade.parse("9")


def test_most_likely_grade_breaks_ties_toward_the_higher_grade() -> None:
    distribution = GradeDistribution.from_mapping({"10": 0.5, "9": 0.5})
    assert distribution.most_likely_grade == Grade.parse("10")


def test_probability_of_an_absent_grade_is_zero() -> None:
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert distribution.probability_of(Grade(Decimal("5"))) == 0.0


def test_the_full_distribution_is_retained_not_collapsed() -> None:
    """spec Â§2.1 â€” the UI may show one grade; the distribution stays whole."""
    distribution = GradeDistribution.from_mapping(SPEC_EXAMPLE)
    assert distribution.as_mapping() == SPEC_EXAMPLE
    assert distribution.most_likely_grade == Grade.parse("9")


def test_equal_distributions_compare_equal() -> None:
    assert GradeDistribution.from_mapping(SPEC_EXAMPLE) == GradeDistribution.from_mapping(
        dict(reversed(list(SPEC_EXAMPLE.items())))
    )


def test_direct_construction_enforces_the_same_invariant() -> None:
    """Bypassing `from_mapping` must not bypass spec Â§63."""
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution({Grade.parse("10"): 0.4, Grade.parse("9"): 0.5})
