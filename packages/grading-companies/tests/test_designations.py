"""The five designations, and which company issues each.

A designation is not a point on a grade scale, and `Grade` stays exactly as
narrow as it was — that is the whole reason this vocabulary exists rather than a
sixth member on the scale.
"""

from __future__ import annotations

import pytest
from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade
from tcg_grading_companies import ADAPTERS, DESIGNATIONS, Designation, GradingCompany

SLUGS = sorted(ADAPTERS)


def test_every_v1_company_issues_at_least_one_designation() -> None:
    assert set(DESIGNATIONS) == {str(company) for company in GradingCompany}
    for slug in SLUGS:
        assert DESIGNATIONS[slug]


def test_no_designation_belongs_to_two_companies() -> None:
    """A label is printed by one company, and reading it as another's is a bug."""
    issued = [designation for slug in SLUGS for designation in DESIGNATIONS[slug]]

    assert sorted(issued) == sorted(Designation)


def test_a_company_does_not_issue_another_companys_designation() -> None:
    """The case that makes the mapping worth having rather than one flat set."""
    assert Designation.BLACK_LABEL not in DESIGNATIONS["psa"]
    assert Designation.AUTHENTIC not in DESIGNATIONS["bgs"]
    assert Designation.PRISTINE_10 not in DESIGNATIONS["psa"]


def test_the_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        DESIGNATIONS["cgc"] = frozenset()  # type: ignore[index]


def test_a_designation_is_a_plain_string_and_never_a_grade() -> None:
    """`Grade` is unchanged, which is the property this vocabulary protects.

    A `Decimal` multiple of 0.5 in [0, 10] is what makes a grade usable as a
    distribution key and a database key; "authentic" is neither.
    """
    assert str(Designation.AUTHENTIC) == "authentic"

    for designation in Designation:
        with pytest.raises(InvalidGrade):
            Grade.parse(str(designation))
