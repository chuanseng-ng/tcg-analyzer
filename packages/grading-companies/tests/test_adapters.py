"""The port's four answers and its one refusal."""

from __future__ import annotations

from datetime import date

import pytest
from tcg_grading_companies import (
    ADAPTERS,
    BGSAdapter,
    GradePredictionUnavailable,
    GradingCompany,
    GradingCompanyError,
    PSAAdapter,
    TAGAdapter,
)
from tcg_grading_companies.reference import EMPTY_RULES

SLUGS = sorted(ADAPTERS)


def test_the_registry_covers_exactly_the_v1_companies() -> None:
    assert set(ADAPTERS) == {str(company) for company in GradingCompany}
    assert isinstance(ADAPTERS["psa"], PSAAdapter)
    assert isinstance(ADAPTERS["tag"], TAGAdapter)
    assert isinstance(ADAPTERS["bgs"], BGSAdapter)


def test_the_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        ADAPTERS["cgc"] = PSAAdapter()  # type: ignore[index]


# --------------------------------------------------------------------------
# predict_grade
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_predict_grade_refuses_rather_than_fabricating_a_distribution(slug: str) -> None:
    with pytest.raises(GradePredictionUnavailable) as raised:
        ADAPTERS[slug].predict_grade(object())
    message = str(raised.value)
    assert slug in message
    assert "M8" in message


def test_the_refusal_is_catchable_as_a_not_implemented_error() -> None:
    """Derived from the closest builtin, the way `tcg_domain.errors` are."""
    assert issubclass(GradePredictionUnavailable, NotImplementedError)
    assert issubclass(GradePredictionUnavailable, GradingCompanyError)


# --------------------------------------------------------------------------
# The reference data each adapter carries
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_service_options_are_empty_in_v1(slug: str) -> None:
    """Fees are M5's configurable economic inputs, not a table here."""
    assert ADAPTERS[slug].get_service_options() == ()


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_every_company_grades_pokemon_and_says_nothing_more(slug: str) -> None:
    assert ADAPTERS[slug].get_supported_card_types() == ("pokemon",)


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_rules_carry_a_readable_source_and_the_date_it_was_read(slug: str) -> None:
    rules = ADAPTERS[slug].get_rules()
    assert rules.source.startswith("https://")
    assert rules.verified_on is not None
    assert rules.version.endswith(rules.verified_on.isoformat())


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_the_rules_body_is_deliberately_empty(slug: str) -> None:
    """The published standards are the companies' copyrighted text; what the
    product needs, and keeps, is the version identifier and a pointer."""
    assert ADAPTERS[slug].get_rules().rules == EMPTY_RULES


def test_the_empty_rules_body_cannot_be_mutated_into_one_companys_rules() -> None:
    with pytest.raises(TypeError):
        ADAPTERS["psa"].get_rules().rules["centering"] = "60/40"  # type: ignore[index]


def test_psa_records_the_date_its_scale_took_effect() -> None:
    """PSA states one — 1 February 2008, when the half-point scale began."""
    assert ADAPTERS["psa"].get_rules().effective_from == date(2008, 2, 1)


@pytest.mark.parametrize("slug", ("tag", "bgs"), ids=str)
def test_a_company_that_states_no_effective_date_records_none(slug: str) -> None:
    """Absent rather than invented. If a company starts publishing one, fill it
    in here rather than inferring it from a copyright footer."""
    assert ADAPTERS[slug].get_rules().effective_from is None


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_every_standard_is_the_current_one(slug: str) -> None:
    """Spec §23 keeps superseded rules; none has been superseded yet."""
    assert ADAPTERS[slug].get_rules().effective_to is None
