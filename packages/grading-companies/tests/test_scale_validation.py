"""Rejecting a distribution keyed with a grade its company cannot issue.

Spec §24's example distribution collapses a tail into ``7_or_lower``, so grade
keys are not simply numbers and "is this key legal?" is not simply membership.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from tcg_domain.distribution import GradeDistribution
from tcg_domain.grade import Grade, GradeBound
from tcg_grading_companies import BGS_SCALE, PSA_SCALE, TAG_SCALE, UnsupportedGrade

#: Spec §24's own example, verbatim.
SPEC_EXAMPLE = {"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}


@pytest.mark.parametrize("scale", (PSA_SCALE, TAG_SCALE, BGS_SCALE), ids=lambda s: s.company)
def test_the_spec_example_is_valid_for_every_company(scale) -> None:
    scale.validate(GradeDistribution.from_mapping(SPEC_EXAMPLE))


def test_a_psa_distribution_naming_9_5_is_rejected() -> None:
    distribution = GradeDistribution.from_mapping({"9.5": 0.4, "9": 0.6})
    with pytest.raises(UnsupportedGrade) as raised:
        PSA_SCALE.validate(distribution)
    message = str(raised.value)
    assert "psa" in message
    assert "9.5" in message


def test_the_same_distribution_is_valid_for_bgs() -> None:
    """The scales are the whole of the difference — no per-company special case."""
    BGS_SCALE.validate(GradeDistribution.from_mapping({"9.5": 0.4, "9": 0.6}))


def test_a_grade_below_the_scale_is_rejected() -> None:
    distribution = GradeDistribution.from_mapping({"0.5": 0.5, "1": 0.5})
    with pytest.raises(UnsupportedGrade, match=re.escape("0.5")):
        BGS_SCALE.validate(distribution)


# --------------------------------------------------------------------------
# Buckets: legal exactly when their value names a grade on the scale.
# --------------------------------------------------------------------------
def test_a_bucket_over_a_grade_on_the_scale_is_legal() -> None:
    assert PSA_SCALE.supports(Grade(Decimal("7"), GradeBound.OR_LOWER))
    assert PSA_SCALE.supports(Grade(Decimal("9"), GradeBound.OR_HIGHER))


def test_a_bucket_over_a_grade_the_company_cannot_issue_is_not() -> None:
    over_9_5 = Grade(Decimal("9.5"), GradeBound.OR_HIGHER)
    assert not PSA_SCALE.supports(over_9_5)
    assert not TAG_SCALE.supports(over_9_5)
    assert BGS_SCALE.supports(over_9_5)


def test_validate_refuses_an_illegal_bucket_too() -> None:
    distribution = GradeDistribution.from_mapping({"9.5_or_higher": 0.3, "9": 0.7})
    with pytest.raises(UnsupportedGrade, match=re.escape("9.5_or_higher")):
        PSA_SCALE.validate(distribution)
