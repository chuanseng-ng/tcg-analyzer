"""The three grade scales, and the one grade they disagree about.

Every assertion here is a claim about a published vendor standard, read on the
date recorded in `tcg_grading_companies.companies`. If a company revises its
scale these tests should fail — that is the point of them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade, GradeBound
from tcg_grading_companies import (
    ADAPTERS,
    BGS_SCALE,
    PSA_SCALE,
    TAG_SCALE,
    GradeScale,
    GradingCompany,
)

ALL_SCALES = (PSA_SCALE, TAG_SCALE, BGS_SCALE)


def grade(value: str) -> Grade:
    return Grade(Decimal(value))


# --------------------------------------------------------------------------
# The scales themselves
# --------------------------------------------------------------------------
def test_psa_runs_from_1_to_10_with_half_points_and_no_9_5() -> None:
    expected = {grade(f"{n / 2:g}") for n in range(2, 19)} | {grade("10")}
    assert PSA_SCALE.grades == expected
    assert len(PSA_SCALE.grades) == 18


def test_tag_has_the_same_shape_as_psa() -> None:
    assert TAG_SCALE.grades == PSA_SCALE.grades


def test_bgs_runs_from_1_to_10_in_half_points_throughout() -> None:
    expected = {grade(f"{n / 2:g}") for n in range(2, 21)}
    assert BGS_SCALE.grades == expected
    assert len(BGS_SCALE.grades) == 19


def test_only_bgs_issues_a_9_5() -> None:
    """The one asymmetry between the three companies, asserted on its own.

    The common summary — "BGS has half grades, PSA and TAG don't" — is wrong,
    and code written to it refuses a PSA 8.5.
    """
    assert grade("9.5") in BGS_SCALE.grades
    assert grade("9.5") not in PSA_SCALE.grades
    assert grade("9.5") not in TAG_SCALE.grades


@pytest.mark.parametrize("scale", ALL_SCALES, ids=lambda s: s.company)
def test_every_company_issues_half_grades(scale: GradeScale) -> None:
    assert grade("8.5") in scale.grades
    assert grade("1.5") in scale.grades


@pytest.mark.parametrize("scale", ALL_SCALES, ids=lambda s: s.company)
def test_no_scale_reaches_below_1(scale: GradeScale) -> None:
    """A step of 0.5 is not a grade of 0.5: the floor is 1 for all three."""
    assert min(scale.grades) == grade("1")
    assert max(scale.grades) == grade("10")


@pytest.mark.parametrize("scale", ALL_SCALES, ids=lambda s: s.company)
def test_a_scale_names_points_never_buckets(scale: GradeScale) -> None:
    assert all(not item.is_bucket for item in scale.grades)


def test_the_scales_cover_exactly_the_v1_companies() -> None:
    assert {scale.company for scale in ALL_SCALES} == {str(c) for c in GradingCompany}


# --------------------------------------------------------------------------
# Versioning
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slug", sorted(ADAPTERS), ids=str)
def test_a_scale_and_its_rules_share_one_version(slug: str) -> None:
    """One version per company's published reference data, not two namespaces."""
    adapter = ADAPTERS[slug]
    assert adapter.get_grade_scale().version == adapter.get_rules().version


@pytest.mark.parametrize("slug", sorted(ADAPTERS), ids=str)
def test_a_company_names_itself_the_same_way_everywhere(slug: str) -> None:
    adapter = ADAPTERS[slug]
    assert adapter.company == slug
    assert adapter.get_grade_scale().company == slug
    assert adapter.get_rules().company == slug


# --------------------------------------------------------------------------
# Ordering, and the type's own guards
# --------------------------------------------------------------------------
def test_ordered_sorts_half_grades_between_their_neighbours() -> None:
    ordered = BGS_SCALE.ordered
    assert ordered[:4] == (grade("1"), grade("1.5"), grade("2"), grade("2.5"))
    assert ordered[-3:] == (grade("9"), grade("9.5"), grade("10"))


def test_an_empty_scale_is_rejected() -> None:
    with pytest.raises(InvalidGrade, match="at least one grade"):
        GradeScale(company="psa", version="v", grades=frozenset())


def test_a_bucket_grade_cannot_be_a_point_on_a_scale() -> None:
    bucket = Grade(Decimal("7"), GradeBound.OR_LOWER)
    with pytest.raises(InvalidGrade, match="names points, not collapsed tails"):
        GradeScale(company="psa", version="v", grades=frozenset({bucket}))
