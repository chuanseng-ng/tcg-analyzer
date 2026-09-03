"""The port's four answers, its one refusal, and the seam its fifth answer comes through."""

from __future__ import annotations

from datetime import date

import pytest
from tcg_domain.analysis import V1_SIDES
from tcg_domain.condition import ConditionAssessment
from tcg_domain.confidence import INSUFFICIENT_INFORMATION, Confidence, InsufficientInformation
from tcg_domain.distribution import GradeDistribution
from tcg_grading_companies import (
    ADAPTERS,
    BGSAdapter,
    GradePrediction,
    GradePredictionFailed,
    GradePredictionUnavailable,
    GradePredictor,
    GradingCompany,
    GradingCompanyError,
    PSAAdapter,
    TAGAdapter,
    UnsupportedGrade,
)
from tcg_grading_companies.reference import EMPTY_RULES

SLUGS = sorted(ADAPTERS)

#: The thinnest assessment the domain admits — every axis refused. #180 makes it
#: constructible on purpose, and it is what proves the port asks for a
#: `ConditionAssessment` rather than for a *measured* one.
REFUSED_ASSESSMENT = ConditionAssessment(
    centering=INSUFFICIENT_INFORMATION,
    corners=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
    edges=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
    surface=dict.fromkeys(V1_SIDES, INSUFFICIENT_INFORMATION),
    manufacturing_defects=INSUFFICIENT_INFORMATION,
    eye_appeal=INSUFFICIENT_INFORMATION,
    confidence=Confidence(0.0),
)


def test_the_registry_covers_exactly_the_v1_companies() -> None:
    assert set(ADAPTERS) == {str(company) for company in GradingCompany}
    assert isinstance(ADAPTERS["psa"], PSAAdapter)
    assert isinstance(ADAPTERS["tag"], TAGAdapter)
    assert isinstance(ADAPTERS["bgs"], BGSAdapter)


def test_the_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        ADAPTERS["cgc"] = PSAAdapter()  # type: ignore[index]


# --------------------------------------------------------------------------
# predict_grade — the seam, exercised with stubs
# --------------------------------------------------------------------------
# Stubs rather than the three real predictors: `ml/grading/*` depend on this
# package and never the reverse (ADR 0011 decision 5), and a test importing
# `tcg_ml_grading_psa` would be a dependency nothing declares. The real three
# are bound to the adapters at the repository root, in
# `tests/test_adapters_carry_the_predictors.py`, for the same reason
# `test_grade_predictors_differ.py` lives there.
ADAPTER_CLASSES = {"psa": PSAAdapter, "tag": TAGAdapter, "bgs": BGSAdapter}


def _answering(distribution: GradeDistribution) -> GradePredictor:
    def predictor(condition: ConditionAssessment) -> GradePrediction:
        return GradePrediction(
            grade_probability=distribution,
            model_confidence=Confidence(0.2),
            model_version="grading-stub-v0.0.0",
        )

    return predictor


def _refusing(condition: ConditionAssessment) -> InsufficientInformation:
    return INSUFFICIENT_INFORMATION


def _raising(error: BaseException) -> GradePredictor:
    def predictor(condition: ConditionAssessment) -> GradePrediction:
        raise error

    return predictor


TEN = GradeDistribution.from_mapping({"10": 1.0})
NINE_POINT_FIVE = GradeDistribution.from_mapping({"9.5": 1.0})


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_an_adapter_built_without_a_model_refuses_rather_than_fabricating(slug: str) -> None:
    """`ADAPTERS` is what the API image imports, and it carries no predictor."""
    with pytest.raises(GradePredictionUnavailable) as raised:
        ADAPTERS[slug].predict_grade(REFUSED_ASSESSMENT)
    message = str(raised.value)
    assert slug in message
    assert "grading model" in message


def test_the_refusal_is_catchable_as_a_not_implemented_error() -> None:
    """Derived from the closest builtin, the way `tcg_domain.errors` are."""
    assert issubclass(GradePredictionUnavailable, NotImplementedError)
    assert issubclass(GradePredictionUnavailable, GradingCompanyError)


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_an_adapter_built_with_a_model_answers_with_it(slug: str) -> None:
    adapter = ADAPTER_CLASSES[slug](predictor=_answering(TEN))

    prediction = adapter.predict_grade(REFUSED_ASSESSMENT)

    assert isinstance(prediction, GradePrediction)
    assert prediction.grade_probability == TEN
    assert prediction.model_version == "grading-stub-v0.0.0"
    assert adapter.company == slug


@pytest.mark.parametrize("slug", SLUGS, ids=str)
def test_a_models_refusal_is_a_result_and_not_an_exception(slug: str) -> None:
    """The two paths, distinguished: "cannot say" returns, "no model" raises."""
    adapter = ADAPTER_CLASSES[slug](predictor=_refusing)

    assert adapter.predict_grade(REFUSED_ASSESSMENT) is INSUFFICIENT_INFORMATION


def test_a_models_own_exception_surfaces_as_a_package_error_and_never_leaks() -> None:
    """The port's standing rule: implementations raise only this package's types."""
    cause = RuntimeError("weights not loaded")
    adapter = PSAAdapter(predictor=_raising(cause))

    with pytest.raises(GradePredictionFailed) as raised:
        adapter.predict_grade(REFUSED_ASSESSMENT)

    assert isinstance(raised.value, GradingCompanyError)
    assert raised.value.__cause__ is cause
    assert "psa" in str(raised.value)


def test_a_package_error_from_the_model_is_not_wrapped_a_second_time() -> None:
    error = UnsupportedGrade("already the port's vocabulary")
    adapter = TAGAdapter(predictor=_raising(error))

    with pytest.raises(UnsupportedGrade) as raised:
        adapter.predict_grade(REFUSED_ASSESSMENT)

    assert raised.value is error


def test_the_distribution_is_validated_against_that_adapters_own_scale() -> None:
    """A 9.5 is a BGS grade and not a PSA one, whatever was injected."""
    assert (
        BGSAdapter(predictor=_answering(NINE_POINT_FIVE)).predict_grade(REFUSED_ASSESSMENT)
        is not INSUFFICIENT_INFORMATION
    )

    with pytest.raises(UnsupportedGrade, match=r"psa does not issue grade 9\.5"):
        PSAAdapter(predictor=_answering(NINE_POINT_FIVE)).predict_grade(REFUSED_ASSESSMENT)


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
