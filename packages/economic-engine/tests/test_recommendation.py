"""Spec §44's recommendation engine — `grade | do_not_grade | insufficient_information`.

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance criterion
is that the economics are "independently unit-tested against manually calculated
fixtures", so a number that came out of the implementation is not evidence. The
three-company fixture is `tests/test_strategies.py`'s, whose profits are already
pinned by `tests/test_roi.py` and whose PSA row is ADR 0007's own example 1.

```text
                  PSA          TAG          BGS
distribution      9: .5        9: .2        9: .7,  9.5: .2
                  10: .5       10: .8       10: .1
ladder            200 / 320    80 / 120     150 / 200 / 400
grading_costs     60.00        40.00        25.00
raw price         100.00       100.00       100.00

graded_proceeds   260.00       112.00       185.00
incremental       +100.00      -28.00       +60.00
```

Four tests carry the issue's acceptance criterion.
`test_the_mode_picks_the_company_and_the_economics_pick_the_action` fails if the
two non-economic modes are ever allowed to decide an action, or the economics an
order. `test_the_reason_names_the_figure_that_decided_it` is §50 —  it asserts
the reason's own fields *are* the winner's numbers, not that a string was
produced. `test_no_company_is_named_when_the_action_is_the_admission` is §44's
non-goal made structural. `test_every_gate_is_reachable` is the issue's warning
that a gate which never fires is miscalibrated rather than safe.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Final

import pytest
from tcg_domain import (
    Confidence,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    Money,
)
from tcg_economic_engine import (
    DEFAULT_THRESHOLDS,
    CompanyComparison,
    CompanyOutlook,
    CostConfiguration,
    GradedPrice,
    IncrementalGradingDecision,
    InvalidRecommendationThresholds,
    Reason,
    Recommendation,
    RecommendationThresholds,
    RecommendedAction,
    SellingFee,
    company_outlook,
    recommend,
    strategy_for,
)

CERTAIN: Final = Confidence.of(1.0)

#: A photograph the §19 gate was happy with. Every test that is not about image
#: quality passes this, so the gate it feeds is never the reason a test fails.
SHARP: Final = Confidence.of(0.95)

#: "Leave this argument at the fixture's own value" — `test_strategies.py`'s
#: sentinel, for its reason: `None` is the meaningful value for `raw_price`, and
#: telling the two apart is the whole of #60's `no_raw_price_available`.
_KEEP: Final = object()

CARDS: Final = {
    "psa": (
        {"9": 0.5, "10": 0.5},
        {"9": "200.00", "10": "320.00"},
        ("40.00", "12.00", "8.00"),
    ),
    "tag": (
        {"9": 0.2, "10": 0.8},
        {"9": "80.00", "10": "120.00"},
        ("20.00", "12.00", "8.00"),
    ),
    "bgs": (
        {"9": 0.7, "9.5": 0.2, "10": 0.1},
        {"9": "150.00", "9.5": "200.00", "10": "400.00"},
        ("15.00", "5.00", "5.00"),
    ),
}


def distribution(mapping: dict[str, float]) -> GradeDistribution:
    return GradeDistribution.from_mapping(mapping)


def prices(mapping: dict[str, str], confidence: Confidence = CERTAIN) -> dict[Grade, GradedPrice]:
    return {
        Grade.parse(key): GradedPrice(Money.of(amount), confidence)
        for key, amount in mapping.items()
    }


def raw(amount: str, confidence: Confidence = CERTAIN) -> GradedPrice:
    return GradedPrice(Money.of(amount), confidence)


def costing(grading_fee: str, outbound: str, inbound: str, rate: str = "0") -> CostConfiguration:
    """A configuration whose `grading_costs` is exactly the three named sums."""
    return CostConfiguration(
        grading_fee=Money.of(grading_fee),
        outbound_shipping=Money.of(outbound),
        return_shipping=Money.of(inbound),
        insurance=Money.zero(),
        miscellaneous=Money.zero(),
        selling_fee=SellingFee(rate=Decimal(rate)),
    )


def company(
    slug: str,
    *,
    costs: CostConfiguration | None = None,
    ladder: dict[str, str] | None = None,
    grades: dict[str, float] | None = None,
    raw_price: object = _KEEP,
    acquisition_cost: Money | None = None,
    price_confidence: Confidence = CERTAIN,
    distribution_confidence: Confidence = CERTAIN,
) -> CompanyOutlook:
    """One company's outlook on the fixture card, with anything overridable."""
    fixture_grades, fixture_ladder, line_items = CARDS[slug]
    return company_outlook(
        slug,
        distribution(grades if grades is not None else fixture_grades),
        prices(ladder if ladder is not None else fixture_ladder, price_confidence),
        raw("100.00") if raw_price is _KEEP else raw_price,  # type: ignore[arg-type]
        acquisition_cost,
        costs if costs is not None else costing(*line_items),
        distribution_confidence=distribution_confidence,
    )


def three_companies() -> tuple[CompanyOutlook, ...]:
    return (company("psa"), company("tag"), company("bgs"))


def advise(
    outlooks: tuple[CompanyOutlook, ...] | None = None,
    mode: str = "expected_profit",
    *,
    image_quality: Confidence = SHARP,
    thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS,
) -> Recommendation:
    return recommend(
        three_companies() if outlooks is None else outlooks,
        strategy_for(mode),
        image_quality=image_quality,
        thresholds=thresholds,
    )


def decided(outlook: CompanyOutlook) -> IncrementalGradingDecision:
    """Narrow an outlook's incremental figure, asserting it is one."""
    figure = outlook.incremental
    assert isinstance(figure, IncrementalGradingDecision), figure
    return figure


# --------------------------------------------------------------------------
# The three actions
# --------------------------------------------------------------------------


def test_clear_positive_economics_with_good_confidence_yields_grade() -> None:
    """PSA: 0.5·200 + 0.5·320 = 260; 260 - 100 - 60 = +100.00, well over 5.00."""
    result = advise()

    assert result.recommended_action is RecommendedAction.GRADE
    assert result.recommended_company == "psa"
    assert result.reason.code == "profit_clears_margin"
    assert result.reason.value == Money.of("100.00")
    assert result.failed_gates == ()


def test_clear_negative_economics_yields_do_not_grade() -> None:
    """TAG alone: 0.2·80 + 0.8·120 = 112; 112 - 100 - 40 = -28.00."""
    result = advise((company("tag"),))

    assert result.recommended_action is RecommendedAction.DO_NOT_GRADE
    assert result.recommended_company == "tag"
    assert result.reason.code == "profit_below_margin"
    assert result.reason.value == Money.of("-28.00")
    assert result.reason.threshold == Money.of("5.00")


def test_low_condition_confidence_yields_insufficient_information() -> None:
    """A model trusted 0.40 is below the 0.50 floor, whatever the economics say."""
    result = advise((company("psa", distribution_confidence=Confidence.of(0.4)),))

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "grade_confidence_below_threshold"
    assert result.reason.figure == "distribution_confidence"
    assert result.reason.value == Confidence.of(0.4)


def test_missing_graded_prices_yield_insufficient_information() -> None:
    """Half of PSA's distribution is unpriced — 0.50 against a 0.25 ceiling.

    The expectation is still *computable*: #59 excludes the unpriced grade and
    renormalises, reporting 200.00 conditional on a nine. Reporting that as an
    answer is what this gate refuses.
    """
    result = advise((company("psa", ladder={"9": "200.00"}),))

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "unpriced_probability_too_high"
    assert result.reason.figure == "unpriced_probability"
    assert result.reason.value == pytest.approx(0.5)
    assert result.reason.threshold == pytest.approx(0.25)


def test_a_poor_photograph_yields_insufficient_information() -> None:
    """The economics are excellent and the image is not. §19's gate lets `poor`
    through with a warning, which is exactly the case §44 must not force."""
    result = advise(image_quality=Confidence.of(0.31))

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "image_quality_below_threshold"
    assert result.reason.value == Confidence.of(0.31)


def test_every_gate_is_reachable() -> None:
    """The issue's own warning: a gate that never fires is miscalibrated.

    Every `code` the module can emit is produced here by ordinary inputs — a
    missing raw price, a thin ladder, an unmeasured model — none of them exotic.
    """
    fired = {
        advise(()).reason.code,
        advise(image_quality=Confidence.of(0.1)).reason.code,
        advise((company("psa", distribution_confidence=Confidence.of(0.1)),)).reason.code,
        advise((company("tag", raw_price=None),), "highest_grade_probability").reason.code,
        advise((company("psa", ladder={"9": "200.00"}),)).reason.code,
        advise((company("psa", price_confidence=Confidence.of(0.2)),)).reason.code,
        advise().reason.code,
        advise((company("tag"),)).reason.code,
    }

    assert fired == {
        "no_company_can_be_ranked",
        "image_quality_below_threshold",
        "grade_confidence_below_threshold",
        "no_raw_price_available",
        "unpriced_probability_too_high",
        "figure_confidence_below_threshold",
        "profit_clears_margin",
        "profit_below_margin",
    }


# --------------------------------------------------------------------------
# The mode picks the company; the economics pick the action
# --------------------------------------------------------------------------


def test_the_mode_picks_the_company_and_the_economics_pick_the_action() -> None:
    """TAG is the cheapest to submit to and grading it earns nothing.

    TAG at 12.00 of line items: 112 - 100 - 12 = 0.00, against PSA's 60.00 and
    BGS's 25.00. `lowest_total_cost` names TAG — that is the preference the user
    expressed and #63 binds that no economics may be blended back into it — and
    the action is still `do_not_grade`, because "which company?" and "should I
    grade at all?" are two questions §44 asks separately.
    """
    result = advise(
        (
            company("psa"),
            company("tag", costs=costing("10.00", "1.00", "1.00")),
            company("bgs"),
        ),
        "lowest_total_cost",
    )

    assert result.recommended_company == "tag"
    assert result.recommended_action is RecommendedAction.DO_NOT_GRADE
    assert result.reason.value == Money.of("0.00")


def test_the_action_is_the_winners_economics_under_every_mode() -> None:
    """Whichever mode ranks, the action is read off the company it named."""
    for mode in ("expected_profit", "roi", "highest_grade_probability", "expected_graded_value"):
        result = advise(mode=mode)
        winner = next(one for one in three_companies() if one.company == result.recommended_company)

        assert result.reason.value == decided(winner).incremental_profit, mode


def test_an_admission_propagates_wearing_its_own_reason() -> None:
    """No raw price: the mode still answers and the economics cannot.

    `highest_grade_probability` never returns an admission — a distribution is
    never empty — so TAG ranks on P(10) = 0.8 while §41's incremental figure is
    undefined. #60's own string reaches the reason unaltered.
    """
    result = advise((company("tag", raw_price=None),), "highest_grade_probability")

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "no_raw_price_available"
    assert result.reason.figure == "incremental_profit"
    assert result.reason.value is None
    assert result.reason.threshold is None


def test_nothing_rankable_is_an_admission_not_an_empty_recommendation() -> None:
    outlooks = tuple(company(slug, raw_price=None) for slug in ("psa", "tag", "bgs"))
    result = advise(outlooks)

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "no_company_can_be_ranked"
    assert isinstance(result.comparison, InsufficientInformation)


def test_no_company_is_named_when_the_action_is_the_admission() -> None:
    """§44's non-goal, structurally. A company beside "we cannot tell" is read as
    a recommendation, which is the forcing the spec forbids — but the comparison
    is still carried, so §49's compare table renders either way."""
    result = advise(image_quality=Confidence.of(0.1))

    assert result.recommended_company is None
    assert isinstance(result.comparison, CompanyComparison)
    assert [entry.company for entry in result.comparison.ranked] == ["psa", "bgs", "tag"]


def test_a_tie_at_the_top_is_not_downgraded_to_an_admission() -> None:
    """Two companies with identical costs tie on `lowest_total_cost`.

    An arbitrary choice among equals is not a data-quality problem, and #63's
    `tied_at_the_top` is already the field that says the order means nothing.
    """
    same = costing("20.00", "0.00", "0.00")
    result = advise((company("psa", costs=same), company("bgs", costs=same)), "lowest_total_cost")

    assert result.recommended_action is RecommendedAction.GRADE
    assert isinstance(result.comparison, CompanyComparison)
    assert result.comparison.tied_at_the_top == ("bgs", "psa")


# --------------------------------------------------------------------------
# §50 — the reason is the comparison that fired
# --------------------------------------------------------------------------


def test_the_reason_names_the_figure_that_decided_it() -> None:
    """Not "a string was produced": the reason's fields *are* PSA's numbers."""
    winner = company("psa")
    result = advise((winner,))
    figure = decided(winner)

    assert result.reason.figure == "incremental_profit"
    assert result.reason.value == figure.incremental_profit
    assert result.reason.threshold == DEFAULT_THRESHOLDS.minimum_incremental_profit
    assert str(figure.incremental_profit) in str(result.reason)


def test_every_failed_gate_is_carried_and_the_decisive_one_is_first() -> None:
    """A poor photograph *and* half the ladder missing. Fixing one leaves the
    other, and a user who is told only the first hits the second wall blind."""
    result = advise(
        (company("psa", ladder={"9": "200.00"}),),
        image_quality=Confidence.of(0.31),
    )

    assert [gate.code for gate in result.failed_gates] == [
        "image_quality_below_threshold",
        "unpriced_probability_too_high",
    ]
    assert result.reason == result.failed_gates[0]


def test_a_successful_recommendation_carries_no_failed_gates() -> None:
    assert advise().failed_gates == ()
    assert advise((company("tag"),)).failed_gates == ()


def test_the_recommendation_recomputes_nothing() -> None:
    """#62's and #63's rule one layer up: every number is read off the outlook."""
    winner = company("psa")
    result = advise((winner,))
    figure = decided(winner)

    assert result.reason.value == figure.incremental_profit
    assert result.figure_confidence == figure.confidence
    assert result.grade_confidence == winner.distribution_confidence


def test_no_figure_here_is_called_roi_alone() -> None:
    """ADR 0007's rule survives every layer — `test_roi.py`'s assertion, here."""
    import tcg_economic_engine.recommendation as module

    fields = {
        field.name for shape in (Recommendation, Reason) for field in dataclasses.fields(shape)
    }

    assert "roi" not in fields
    assert "roi" not in module.__all__


# --------------------------------------------------------------------------
# Confidence — three sources, combined explicitly
# --------------------------------------------------------------------------


def test_the_three_confidence_sources_are_carried_separately() -> None:
    """§44 outputs one `confidence`; the issue requires the three sources behind
    it stay legible rather than averaged into one opaque score."""
    result = advise((company("psa", distribution_confidence=Confidence.of(0.8)),))

    assert result.image_quality == SHARP
    assert result.grade_confidence == Confidence.of(0.8)
    assert result.figure_confidence == Confidence.of(0.8)
    assert result.confidence == Confidence.of(0.8)


def test_the_confidence_is_the_weakest_source_never_a_product() -> None:
    """0.8 and 0.95 give 0.8, not 0.76. Compounding a fourth factor onto the
    three #59 already multiplies is the miscalibration its own note flags."""
    result = advise(
        (company("psa", distribution_confidence=Confidence.of(0.8)),),
        image_quality=Confidence.of(0.95),
    )

    assert result.confidence == Confidence.of(0.8)


def test_with_no_winner_the_confidence_is_the_image_alone() -> None:
    """Nothing ranked, so there is no model and no ladder to discount by."""
    result = advise((), image_quality=Confidence.of(0.72))

    assert result.confidence == Confidence.of(0.72)
    assert result.grade_confidence is None
    assert result.figure_confidence is None


def test_a_stale_ladder_alone_can_decide_it() -> None:
    """PSA's model is certain and its prices are not: 1.0 · (0.5·0.2 + 0.5·0.2)
    = 0.20, under the 0.40 floor. The grade-confidence gate passes; this one
    does not, which is what gating the two separately buys."""
    result = advise((company("psa", price_confidence=Confidence.of(0.2)),))

    assert result.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION
    assert result.reason.code == "figure_confidence_below_threshold"
    assert result.reason.value == Confidence.of(0.2)


# --------------------------------------------------------------------------
# Thresholds — configurable, and their boundaries
# --------------------------------------------------------------------------


def test_meeting_a_minimum_exactly_passes() -> None:
    """Thresholds are minimums, so equality passes — `Confidence.is_below` is
    strict and the margin comparison matches it."""
    result = advise((company("psa", distribution_confidence=Confidence.of(0.5)),))

    assert result.recommended_action is RecommendedAction.GRADE


def test_the_profit_margin_boundary_is_the_margin_itself() -> None:
    """TAG at 7.00 of line items: 112 - 100 - 7 = 5.00, exactly the margin. One
    cent more of cost and grading no longer clears it."""
    at_margin = advise((company("tag", costs=costing("7.00", "0.00", "0.00")),))
    below = advise((company("tag", costs=costing("7.01", "0.00", "0.00")),))

    assert at_margin.reason.value == Money.of("5.00")
    assert at_margin.recommended_action is RecommendedAction.GRADE
    assert below.reason.value == Money.of("4.99")
    assert below.recommended_action is RecommendedAction.DO_NOT_GRADE


def test_the_unpriced_ceiling_boundary_is_the_ceiling_itself() -> None:
    """A quarter of the distribution unpriced is admitted; more is not."""
    at_ceiling = advise((company("psa", grades={"9": 0.75, "10": 0.25}, ladder={"9": "200.00"}),))
    over = advise((company("psa", grades={"9": 0.7, "10": 0.3}, ladder={"9": "200.00"}),))

    assert at_ceiling.recommended_action is RecommendedAction.GRADE
    assert over.recommended_action is RecommendedAction.INSUFFICIENT_INFORMATION


def test_a_thin_profit_is_do_not_grade_rather_than_grade() -> None:
    """TAG at 10.00: 112 - 100 - 10 = +2.00. Positive, and not worth an envelope
    — the margin is a margin of safety rather than a sign test."""
    result = advise((company("tag", costs=costing("10.00", "0.00", "0.00")),))

    assert result.recommended_action is RecommendedAction.DO_NOT_GRADE
    assert result.reason.value == Money.of("2.00")


def test_thresholds_are_configurable() -> None:
    """The same inputs, two policies. A house that will grade on any positive
    edge gets `grade`; the default does not."""
    permissive = dataclasses.replace(
        DEFAULT_THRESHOLDS, minimum_incremental_profit=Money.of("0.00")
    )
    outlooks = (company("tag", costs=costing("10.00", "0.00", "0.00")),)

    assert advise(outlooks, thresholds=permissive).recommended_action is RecommendedAction.GRADE
    assert advise(outlooks).recommended_action is RecommendedAction.DO_NOT_GRADE


def test_the_defaults_are_the_documented_ones() -> None:
    """They are a product decision, so they are pinned rather than assumed."""
    assert DEFAULT_THRESHOLDS.minimum_image_quality == Confidence.of(0.5)
    assert DEFAULT_THRESHOLDS.minimum_grade_confidence == Confidence.of(0.5)
    assert DEFAULT_THRESHOLDS.minimum_figure_confidence == Confidence.of(0.4)
    assert DEFAULT_THRESHOLDS.maximum_unpriced_probability == pytest.approx(0.25)
    assert DEFAULT_THRESHOLDS.minimum_incremental_profit == Money.of("5.00")


@pytest.mark.parametrize("fraction", [-0.01, 1.5, float("nan"), "0.25", True])
def test_an_unreadable_unpriced_ceiling_is_refused(fraction: object) -> None:
    """#65 will take this from configuration, which makes it a trust boundary."""
    with pytest.raises(InvalidRecommendationThresholds):
        dataclasses.replace(DEFAULT_THRESHOLDS, maximum_unpriced_probability=fraction)  # type: ignore[arg-type]
