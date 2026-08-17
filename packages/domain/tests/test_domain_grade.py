"""`Grade` â€” the key forms spec Â§24 actually produces, and their ordering."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from tcg_domain import Grade, GradeBound, InvalidGrade

# --------------------------------------------------------------------------
# Parsing and round-tripping
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["10", "9", "9.5", "0", "0.5", "7_or_lower", "9_or_higher"])
def test_parse_and_str_are_inverses(key: str) -> None:
    assert str(Grade.parse(key)) == key


def test_parse_whole_grade() -> None:
    grade = Grade.parse("10")
    assert grade.value == Decimal("10")
    assert grade.bound is GradeBound.EXACT
    assert not grade.is_bucket


def test_parse_half_grade() -> None:
    """BGS must support half grades â€” spec Â§24."""
    grade = Grade.parse("9.5")
    assert grade.value == Decimal("9.5")
    assert grade.bound is GradeBound.EXACT


def test_parse_bucketed_tail() -> None:
    grade = Grade.parse("7_or_lower")
    assert grade.value == Decimal("7")
    assert grade.bound is GradeBound.OR_LOWER
    assert grade.is_bucket


def test_parse_bucketed_head() -> None:
    grade = Grade.parse("9_or_higher")
    assert grade.value == Decimal("9")
    assert grade.bound is GradeBound.OR_HIGHER


def test_parse_accepts_a_half_grade_bucket() -> None:
    assert str(Grade.parse("9.5_or_higher")) == "9.5_or_higher"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "   ",
        "eleven",
        "10.5",  # above the scale
        "-1",
        "9.3",  # not a multiple of 0.5
        "7_or_sideways",
        "7_or_",
        "_or_lower",
        "10.5_or_lower",  # value above the scale
        "9.5.5",
    ],
)
def test_parse_rejects_invalid_keys(key: str) -> None:
    with pytest.raises(InvalidGrade):
        Grade.parse(key)


def test_parse_rejects_a_non_string() -> None:
    with pytest.raises(InvalidGrade):
        Grade.parse(9.5)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def test_construction_rejects_a_float() -> None:
    """Binary floats cannot represent the scale exactly; require Decimal."""
    with pytest.raises(InvalidGrade):
        Grade(1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [9, "9", None])
def test_construction_requires_a_decimal(value: object) -> None:
    """`Grade.parse` is the lenient entry point; the constructor is strict."""
    with pytest.raises(InvalidGrade):
        Grade(value)  # type: ignore[arg-type]


def test_parse_agrees_with_direct_construction() -> None:
    assert Grade.parse("9") == Grade(Decimal("9"))


@pytest.mark.parametrize("value", [Decimal("-0.5"), Decimal("10.5"), Decimal("9.3")])
def test_construction_rejects_out_of_scale_values(value: Decimal) -> None:
    with pytest.raises(InvalidGrade):
        Grade(value)


def test_construction_rejects_a_non_finite_value() -> None:
    with pytest.raises(InvalidGrade):
        Grade(Decimal("NaN"))


def test_trailing_zero_is_canonicalised() -> None:
    assert str(Grade(Decimal("9.0"))) == "9"
    assert Grade(Decimal("9.0")) == Grade(Decimal("9"))
    assert hash(Grade(Decimal("9.0"))) == hash(Grade(Decimal("9")))


def test_grade_is_frozen() -> None:
    grade = Grade.parse("9")
    with pytest.raises(FrozenInstanceError):
        grade.value = Decimal("10")  # type: ignore[misc]


# --------------------------------------------------------------------------
# Ordering â€” the UI orders a distribution without reimplementing this rule.
# --------------------------------------------------------------------------


def test_numeric_grades_sort_by_value() -> None:
    grades = [Grade.parse(key) for key in ["10", "8", "9.5", "9"]]
    assert [str(g) for g in sorted(grades)] == ["8", "9", "9.5", "10"]


def test_bucket_sorts_adjacent_to_its_own_value() -> None:
    grades = [Grade.parse(key) for key in ["9_or_higher", "9", "9_or_lower"]]
    assert [str(g) for g in sorted(grades)] == ["9_or_lower", "9", "9_or_higher"]


def test_mixed_numeric_and_bucket_sort_order() -> None:
    grades = [Grade.parse(key) for key in ["10", "9", "8", "7_or_lower"]]
    assert [str(g) for g in sorted(grades)] == ["7_or_lower", "8", "9", "10"]


def test_bucket_sorts_below_the_next_grade_up() -> None:
    assert Grade.parse("7_or_lower") < Grade.parse("7")
    assert Grade.parse("7_or_lower") < Grade.parse("7.5")
    assert Grade.parse("9_or_higher") > Grade.parse("9")
    assert Grade.parse("9_or_higher") < Grade.parse("9.5")


def test_comparison_with_a_non_grade_is_not_supported() -> None:
    with pytest.raises(TypeError):
        _ = Grade.parse("9") < 9  # type: ignore[operator]


# --------------------------------------------------------------------------
# Exhaustiveness
#
# Both matches below cover every `GradeBound` today, so neither default branch
# is reachable. They exist for the fourth member somebody adds later: without
# them the function falls off the end and returns `None`, which `sort_key`
# would carry into a comparison and `__str__` would turn into a `TypeError`
# several frames away from the omission that caused it.
#
# Reached here through an unbound call with a stand-in, because a `GradeBound`
# that is not one of the three cannot be constructed.
# --------------------------------------------------------------------------
class _NotABound:
    """Stands in for a `GradeBound` member that has no `case` yet."""

    value = Decimal("9")
    bound = "or_sideways"


def test_an_unhandled_bound_fails_loudly_in_sort_offset() -> None:
    with pytest.raises(AssertionError):
        GradeBound.sort_offset.fget(_NotABound.bound)  # type: ignore[attr-defined]


def test_an_unhandled_bound_fails_loudly_in_str() -> None:
    with pytest.raises(AssertionError):
        Grade.__str__(_NotABound())  # type: ignore[arg-type]
