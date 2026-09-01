"""#165's per-company guard and its command line.

The database half lives in `test_datasets_schema.py`, which is where the CHECK
constraints and the write-back are exercised. Everything here is pure: the rule
that a grade belongs to the company that issued it is Python's, deliberately, so
that a fourth company costs no migration of `grading_outcomes`.
"""

from __future__ import annotations

from typing import Any

import pytest
from tcg_api.datasets import outcomes
from tcg_api.datasets.outcomes import GradingOutcomeRefused, verify_outcome
from tcg_grading_companies import Designation

PSA_9: dict[str, Any] = {
    "company": "psa",
    "certification_number": "12345678",
    "grade": "9",
    "designation": None,
}


# ---------------------------------------------------------------------------
# The grade belongs to the company that issued it
# ---------------------------------------------------------------------------
def test_a_grade_on_the_companys_scale_is_accepted() -> None:
    verify_outcome(**PSA_9)


def test_psa_does_not_issue_a_half_point_at_nine() -> None:
    """The one case that proves the scale is per company rather than shared.

    PSA and TAG issue eighteen grades and no 9.5; BGS issues nineteen and has
    one. A single CHECK cannot say that, which is why this is Python.
    """
    with pytest.raises(GradingOutcomeRefused, match=r"does not issue grade 9\.5"):
        verify_outcome(**{**PSA_9, "grade": "9.5"})


def test_bgs_does_issue_a_half_point_at_nine() -> None:
    verify_outcome(**{**PSA_9, "company": "bgs", "grade": "9.5"})


def test_psa_issues_half_points_everywhere_else() -> None:
    """The common summary is wrong for sixteen of PSA's eighteen grades."""
    verify_outcome(**{**PSA_9, "grade": "8.5"})


def test_a_company_with_no_adapter_is_accepted_rather_than_refused() -> None:
    """`GradingCompany` is a vocabulary, and refusing here would close it.

    Spec §22's rule is that a fourth company costs one new adapter and no caller
    change. The table's CHECK still holds the three V1 ships to their slugs; the
    day CGC gets an adapter its grades start being checked, and nothing else
    changes.
    """
    verify_outcome(**{**PSA_9, "company": "cgc", "grade": "9.5"})


def test_a_collapsed_tail_is_not_an_issued_grade() -> None:
    """A slab prints one point; a bucket is what a model emits instead of one."""
    with pytest.raises(GradingOutcomeRefused, match="collapses a tail"):
        verify_outcome(**{**PSA_9, "grade": "7_or_lower"})


def test_something_that_is_not_a_grade_at_all_is_refused() -> None:
    with pytest.raises(GradingOutcomeRefused, match="is not a grade"):
        verify_outcome(**{**PSA_9, "grade": "gem mint"})


# ---------------------------------------------------------------------------
# A designation, which is never a value on a scale
# ---------------------------------------------------------------------------
def test_psa_authentic_carries_no_grade() -> None:
    """V1 does not authenticate, and must still be able to record that PSA did."""
    verify_outcome(
        company="psa",
        certification_number="12345678",
        grade=None,
        designation=str(Designation.AUTHENTIC),
    )


def test_a_designation_the_company_does_not_issue_is_refused() -> None:
    with pytest.raises(GradingOutcomeRefused, match="psa does not issue black_label"):
        verify_outcome(**{**PSA_9, "grade": None, "designation": "black_label"})


def test_an_invented_designation_is_refused() -> None:
    with pytest.raises(GradingOutcomeRefused, match="is not a designation"):
        verify_outcome(**{**PSA_9, "grade": None, "designation": "perfect"})


def test_a_designation_may_accompany_a_grade() -> None:
    """BGS Black Label is a label *on* grade 10, where PSA Authentic replaces one."""
    verify_outcome(company="bgs", certification_number="1", grade="10", designation="black_label")


def test_neither_a_grade_nor_a_designation_is_not_a_submission() -> None:
    with pytest.raises(GradingOutcomeRefused, match="neither a grade nor a designation"):
        verify_outcome(**{**PSA_9, "grade": None})


def test_a_blank_certification_number_is_refused() -> None:
    with pytest.raises(GradingOutcomeRefused, match="certification_number is blank"):
        verify_outcome(**{**PSA_9, "certification_number": "   "})


# ---------------------------------------------------------------------------
# Subgrades — recorded, and never half recorded
# ---------------------------------------------------------------------------
def test_four_subgrades_are_accepted() -> None:
    verify_outcome(
        **{**PSA_9, "company": "bgs", "grade": "10"},
        subgrades=("10", "10", "9.5", "10"),
    )


def test_a_partial_set_of_subgrades_is_refused() -> None:
    """An unrecorded subgrade cannot be recovered; a half-recorded one lies."""
    with pytest.raises(GradingOutcomeRefused, match="all four or none"):
        verify_outcome(**PSA_9, subgrades=("10", "10"))


def test_a_subgrade_is_on_the_issuing_companys_scale_too() -> None:
    with pytest.raises(GradingOutcomeRefused, match=r"does not issue grade 9\.5"):
        verify_outcome(**PSA_9, subgrades=("9.5", "9", "9", "9"))


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
# Parsing and cross-field validation only — `record_outcome` is exercised
# against a live database in `test_datasets_schema.py`, and `main()` is glue, as
# every other domain CLI's `main()` is.
COPY_ID = "6f0b1e3c-6b1a-4f4e-9f2f-0c1d2e3f4a5b"
PSA_ARGV = ("--physical-copy", COPY_ID, "--company", "psa", "--certification-number", "12345678")


def parse(*argv: str) -> Any:
    parser = outcomes._parser()
    arguments = parser.parse_args(list(argv))
    outcomes._validated(parser, arguments)
    return arguments


def test_a_grade_and_a_copy_are_enough() -> None:
    arguments = parse(*PSA_ARGV, "--grade", "9")

    assert str(arguments.physical_copy) == COPY_ID
    assert arguments.returned_at is None


def test_a_return_date_is_optional_because_class_two_has_none() -> None:
    """ADR 0008's approved class 2 is a slab this project did not submit."""
    arguments = parse(*PSA_ARGV, "--grade", "9", "--returned-at", "2026-09-30")

    assert arguments.returned_at.isoformat() == "2026-09-30"


def test_the_command_line_refuses_a_scale_the_company_does_not_have() -> None:
    """`parser.error`, so the operator reads the rule rather than a constraint name."""
    with pytest.raises(SystemExit):
        parse(*PSA_ARGV, "--grade", "9.5")


def test_the_command_line_refuses_a_submission_with_neither() -> None:
    with pytest.raises(SystemExit):
        parse(*PSA_ARGV)


def test_the_command_line_refuses_half_a_set_of_subgrades() -> None:
    with pytest.raises(SystemExit):
        parse(*PSA_ARGV, "--grade", "9", "--subgrade-corners", "9")


def test_a_copy_identifier_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(SystemExit):
        parse("--physical-copy", "copy-1", "--company", "psa", "--certification-number", "1")


def test_the_subgrade_flags_are_the_four_columns() -> None:
    """One list, so a fifth axis cannot appear on the command line and nowhere else."""
    arguments = parse(
        *PSA_ARGV,
        "--company",
        "bgs",
        "--grade",
        "10",
        "--subgrade-centering",
        "10",
        "--subgrade-corners",
        "10",
        "--subgrade-edges",
        "10",
        "--subgrade-surface",
        "9.5",
    )

    assert outcomes._subgrades(arguments) == ("10", "10", "10", "9.5")
