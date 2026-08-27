"""Spec §43's five optimization modes, and spec §49's "Compare PSA / TAG / BGS".

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence. The three-company fixture below was computed on paper and written
before the module it checks; PSA's row is ADR 0007's own example 1, so its
profit and ratio tie back to figures `tests/test_roi.py` already pins.

```text
                  PSA          TAG          BGS
distribution      9: .5        9: .2        9: .7,  9.5: .2
                  10: .5       10: .8       10: .1
ladder            200 / 320    80 / 120     150 / 200 / 400
grading_costs     60.00        40.00        25.00
raw price         100.00       100.00       100.00

graded_proceeds   260.00       112.00       185.00
incremental       +100.00      -28.00       +60.00
incremental_roi   0.6250       -0.2000      0.4800
P(top grade)      0.5          0.8          0.1
```

Four of these are the acceptance criterion itself.
`test_the_same_card_has_three_different_winners` is §43's whole reason to exist —
if one company won everything, the modes would not be five objectives.
`test_a_money_losing_company_still_wins_the_two_non_economic_modes` fails if
somebody "improves" `highest_grade_probability` or `lowest_total_cost` by
blending profit back in. `test_an_undefined_roi_is_unranked_not_sorted_last`
fails if an unanswerable figure is given a sentinel and allowed to sort.
`test_a_sixth_mode_needs_no_change_to_the_engine` fails if a mode ever has to be
registered before it can rank.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    STRATEGIES,
    CompanyComparison,
    CompanyOutlook,
    CostConfiguration,
    ExpectedValue,
    GradedPrice,
    IncrementalGradingDecision,
    IncrementalRoi,
    InvalidComparison,
    OptimizationStrategy,
    RankedCompany,
    SellingFee,
    UnknownOptimizationMode,
    company_outlook,
    rank,
    strategy_for,
)

CERTAIN = Confidence.of(1.0)

#: "Leave this argument at the fixture's own value". `None` cannot serve: it is
#: the meaningful value for `raw_price`, and telling the two apart is the whole
#: of #60's `no_raw_price_available`.
_KEEP: Final = object()

#: The three companies' distributions, ladders and committed line items. One
#: card, three graders, and no two of them agreeing on anything.
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

MODES: Final = (
    "expected_profit",
    "roi",
    "highest_grade_probability",
    "lowest_total_cost",
    "expected_graded_value",
)


def distribution(mapping: dict[str, float]) -> GradeDistribution:
    return GradeDistribution.from_mapping(mapping)


def prices(mapping: dict[str, str], confidence: Confidence = CERTAIN) -> dict[Grade, GradedPrice]:
    """A price ladder from grade keys to decimal strings, all equally trusted."""
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


def compared(result: object) -> CompanyComparison:
    """Assert the result is a comparison rather than an admission, and narrow it."""
    assert isinstance(result, CompanyComparison), result
    return result


def admission(result: object) -> InsufficientInformation:
    assert isinstance(result, InsufficientInformation), result
    return result


def order(result: object) -> list[str]:
    """The ranked companies, best first."""
    return [entry.company for entry in compared(result).ranked]


def under(mode: str, outlooks: Sequence[CompanyOutlook] | None = None) -> object:
    return rank(three_companies() if outlooks is None else outlooks, strategy_for(mode))


# --------------------------------------------------------------------------
# The five modes, against hand-calculated figures
# --------------------------------------------------------------------------


def test_expected_profit_ranks_on_the_incremental_figure() -> None:
    """PSA +100.00, BGS +60.00, TAG -28.00.

    PSA: 0.5·200 + 0.5·320 = 260; 260 - 100 - 60 = 100.00 (ADR 0007 example 1).
    BGS: 0.7·150 + 0.2·200 + 0.1·400 = 185; 185 - 100 - 25 = 60.00.
    TAG: 0.2·80 + 0.8·120 = 112; 112 - 100 - 40 = -28.00.
    """
    result = compared(under("expected_profit"))

    assert order(result) == ["psa", "bgs", "tag"]
    assert [entry.value for entry in result.ranked] == [
        Money.of("100.00"),
        Money.of("60.00"),
        Money.of("-28.00"),
    ]
    assert {entry.figure for entry in result.ranked} == {"incremental_profit"}


def test_roi_ranks_on_the_incremental_ratio() -> None:
    """PSA 0.6250, BGS 0.4800, TAG -0.2000.

    100/160, 60/125 and -28/140 — each denominator the raw opportunity value
    plus that company's own grading costs, per ADR 0007.
    """
    result = compared(under("roi"))

    assert order(result) == ["psa", "bgs", "tag"]
    assert [entry.value for entry in result.ranked] == [
        Decimal("0.6250"),
        Decimal("0.4800"),
        Decimal("-0.2000"),
    ]
    assert {entry.figure for entry in result.ranked} == {"incremental_roi"}


def test_highest_grade_probability_ranks_on_the_top_grade() -> None:
    """TAG 0.8, PSA 0.5, BGS 0.1 — and the grade travels with the number."""
    result = compared(under("highest_grade_probability"))

    assert order(result) == ["tag", "psa", "bgs"]
    assert [entry.value for entry in result.ranked] == [0.8, 0.5, 0.1]
    assert {entry.figure for entry in result.ranked} == {"P(10)"}


def test_lowest_total_cost_ranks_the_cheapest_submission_first() -> None:
    """BGS 25.00, TAG 40.00, PSA 60.00 — the only mode that sorts ascending."""
    result = compared(under("lowest_total_cost"))

    assert order(result) == ["bgs", "tag", "psa"]
    assert [entry.value for entry in result.ranked] == [
        Money.of("25.00"),
        Money.of("40.00"),
        Money.of("60.00"),
    ]
    assert {entry.figure for entry in result.ranked} == {"grading_costs"}


def test_expected_graded_value_ranks_on_proceeds_net_of_the_fee() -> None:
    """PSA 260.00, BGS 185.00, TAG 112.00 — no fee configured, so gross = net."""
    result = compared(under("expected_graded_value"))

    assert order(result) == ["psa", "bgs", "tag"]
    assert [entry.value for entry in result.ranked] == [
        Money.of("260.00"),
        Money.of("185.00"),
        Money.of("112.00"),
    ]
    assert {entry.figure for entry in result.ranked} == {"graded_proceeds"}


def test_expected_graded_value_is_adr_0007s_graded_proceeds() -> None:
    """A ten percent fee inside the sum: 0.5·180 + 0.5·288 = 234.00, not 260.00.

    The fee is netted per outcome *before* the expectation, which is ADR 0007's
    rule satisfied literally rather than merely arithmetically — the cap in
    `SellingFee.on` is what makes the two differ once a fee has a flat part.
    """
    fee_paying = company("psa", costs=costing("40.00", "12.00", "8.00", rate="0.10"))

    result = compared(rank([fee_paying], strategy_for("expected_graded_value")))

    assert result.best.value == Money.of("234.00")


def test_the_same_card_has_three_different_winners() -> None:
    """§43's whole reason to exist: "best" is the user's word, not the engine's."""
    winners = {mode: compared(under(mode)).best.company for mode in MODES}

    assert winners == {
        "expected_profit": "psa",
        "roi": "psa",
        "highest_grade_probability": "tag",
        "lowest_total_cost": "bgs",
        "expected_graded_value": "psa",
    }
    assert len(set(winners.values())) == 3


# --------------------------------------------------------------------------
# The two modes that are not economic objectives
# --------------------------------------------------------------------------


def test_a_money_losing_company_still_wins_the_two_non_economic_modes() -> None:
    """TAG loses 28.00 and wins `highest_grade_probability` anyway.

    The issue says outright that this is a legitimate user preference and that
    the mode "must not quietly fold economics back in". BGS is the same point
    from the other side: cheapest to submit, and not the profitable choice.
    """
    losing = compared(under("expected_profit")).ranked[-1]
    assert losing.company == "tag"
    assert losing.value == Money.of("-28.00")

    assert compared(under("highest_grade_probability")).best.company == "tag"
    assert compared(under("lowest_total_cost")).best.company == "bgs"


def test_lowest_total_cost_is_five_line_items_and_ignores_the_selling_fee() -> None:
    """A fee that would dominate every other cost moves nothing.

    §46's sixth line item is paid out of a sale that may not happen, so it is
    not committed capital — and it depends on the outcome this mode ignores.
    """
    expensive_to_sell = tuple(
        company(slug, costs=costing(*CARDS[slug][2], rate="0.50")) for slug in CARDS
    )

    result = compared(rank(expensive_to_sell, strategy_for("lowest_total_cost")))

    assert order(result) == ["bgs", "tag", "psa"]
    assert result.best.value == Money.of("25.00")


def test_the_top_grade_is_the_distributions_highest_not_its_most_likely() -> None:
    """P(10) = 0.1 on a distribution whose most likely grade is 9."""
    lopsided = company("psa", grades={"9": 0.9, "10": 0.1})

    result = compared(rank([lopsided], strategy_for("highest_grade_probability")))

    assert lopsided.distribution.most_likely_grade == Grade.parse("9")
    assert result.best.value == 0.1
    assert result.best.figure == "P(10)"


def test_a_bucketed_head_is_named_rather_than_compared_as_a_ten() -> None:
    """`9_or_higher` says so in the figure, so nothing reads it as P(10)."""
    collapsed = company(
        "tag",
        grades={"8": 0.3, "9_or_higher": 0.7},
        ladder={"8": "90.00", "9": "200.00"},
    )

    result = compared(rank([collapsed], strategy_for("highest_grade_probability")))

    assert result.best.value == 0.7
    assert result.best.figure == "P(9_or_higher)"


# --------------------------------------------------------------------------
# The incremental basis — the two §41 figures never swap
# --------------------------------------------------------------------------


def test_roi_ranks_on_the_incremental_ratio_whatever_the_user_paid() -> None:
    """An acquisition cost changes the investment figures and no ranking.

    ADR 0007's example 3 is the reason: 0.5600 and 1.2286 describe the same card
    on the same day. A mode whose denominator depended on whether a form field
    had been filled would be the casual choice §42 forbids.
    """
    told_us = tuple(company(slug, acquisition_cost=Money.of("45.00")) for slug in CARDS)

    assert order(under("roi", told_us)) == order(under("roi"))
    assert order(under("expected_profit", told_us)) == order(under("expected_profit"))
    assert {entry.figure for entry in compared(under("roi", told_us)).ranked} == {"incremental_roi"}


def test_no_mode_ranks_on_a_figure_called_roi_alone() -> None:
    """§43's `roi` is a mode name; the number it ranks on is always named."""
    for strategy in STRATEGIES.values():
        result = compared(rank(three_companies(), strategy))
        for entry in result.ranked:
            assert entry.figure != "roi", strategy.mode

    assert [field for field in RankedCompany.__dataclass_fields__ if "roi" in field] == []


def test_the_investment_figures_are_carried_but_never_rank() -> None:
    """#64 reports both §41 figures; only the incremental pair decides an order."""
    investor = company("psa", acquisition_cost=Money.of("45.00"))
    collector = company("psa")

    assert not isinstance(investor.investment_ratio, InsufficientInformation)
    assert admission(collector.investment_ratio).reason == "acquisition_cost_not_supplied"
    assert compared(rank([investor], strategy_for("roi"))).best.value == Decimal("0.6250")


# --------------------------------------------------------------------------
# Ties, and the order that breaks them
# --------------------------------------------------------------------------


def test_ties_break_alphabetically_and_say_so() -> None:
    """Identical costs: bgs, psa, tag — and `tied_at_the_top` names all three."""
    identical = tuple(company(slug, costs=costing("20.00", "10.00", "10.00")) for slug in CARDS)

    result = compared(rank(identical, strategy_for("lowest_total_cost")))

    assert order(result) == ["bgs", "psa", "tag"]
    assert result.tied_at_the_top == ("bgs", "psa", "tag")


def test_a_tie_on_a_descending_mode_breaks_the_same_way() -> None:
    """The `reverse=True` trap: one composite sort key would flip these.

    `expected_graded_value` sorts high to low. Sorting once by ``(value,
    company)`` and reversing puts tag ahead of bgs; a stable sort applied to an
    already-alphabetical list does not, because `reverse=True` leaves equal
    elements alone.
    """
    same_proceeds = tuple(
        company(slug, grades={"10": 1.0}, ladder={"10": "200.00"}) for slug in CARDS
    )

    result = compared(rank(same_proceeds, strategy_for("expected_graded_value")))

    assert order(result) == ["bgs", "psa", "tag"]
    assert result.tied_at_the_top == ("bgs", "psa", "tag")


def test_the_input_order_never_reaches_the_output() -> None:
    """Identical inputs always produce the same recommended company (the issue)."""
    forwards = three_companies()
    backwards = tuple(reversed(forwards))

    for mode in MODES:
        assert order(under(mode, forwards)) == order(under(mode, backwards)), mode


def test_a_unique_leader_is_not_reported_as_tied() -> None:
    """`tied_at_the_top` is empty when the best value belongs to one company."""
    result = compared(under("expected_profit"))

    assert result.best.company == "psa"
    assert result.tied_at_the_top == ()


# --------------------------------------------------------------------------
# Undefined figures — the acceptance criterion
# --------------------------------------------------------------------------


def test_an_undefined_roi_is_unranked_not_sorted_last() -> None:
    """Nothing at risk is not the worst return; it is no return at all.

    PSA's raw price is 0.00 and every line item is zero, so ADR 0007's zero
    guard fires. It must not appear in `ranked` at any position — a sentinel
    sorted last would read as "PSA is the worst choice", which is a claim
    nobody computed.
    """
    nothing_at_risk = company("psa", raw_price=raw("0.00"), costs=costing("0.00", "0.00", "0.00"))

    result = compared(rank((nothing_at_risk, company("tag"), company("bgs")), strategy_for("roi")))

    assert order(result) == ["bgs", "tag"]
    assert result.unranked["psa"].reason == "no_capital_at_risk"
    assert "psa" not in {entry.company for entry in result.ranked}


def test_the_profit_figure_is_still_ranked_when_its_ratio_is_not() -> None:
    """The same company, the same inputs, ranked under one mode and not the other."""
    nothing_at_risk = company("psa", raw_price=raw("0.00"), costs=costing("0.00", "0.00", "0.00"))
    outlooks = (nothing_at_risk, company("tag"), company("bgs"))

    assert order(rank(outlooks, strategy_for("expected_profit"))) == ["psa", "bgs", "tag"]
    assert order(rank(outlooks, strategy_for("roi"))) == ["bgs", "tag"]


def test_each_admission_reaches_the_comparison_wearing_its_own_reason() -> None:
    """Three distinct reasons, and none of them flattened into one."""
    no_raw = company("psa", raw_price=None)
    no_ladder = company("tag", ladder={"5": "80.00"})
    nothing_at_risk = company("bgs", raw_price=raw("0.00"), costs=costing("0.00", "0.00", "0.00"))

    profit = compared(rank((no_raw, no_ladder, company("bgs")), strategy_for("expected_profit")))
    value = compared(
        rank((no_raw, no_ladder, company("bgs")), strategy_for("expected_graded_value"))
    )
    ratio = compared(rank((nothing_at_risk, company("psa")), strategy_for("roi")))

    assert profit.unranked["psa"].reason == "no_raw_price_available"
    assert profit.unranked["tag"].reason == "no_graded_price_available"
    assert value.unranked["tag"].reason == "no_graded_price_available"
    assert ratio.unranked["bgs"].reason == "no_capital_at_risk"
    # A raw price the market has never held stops the profit figure, not the
    # expectation — so this company is unranked under one mode and ranked here.
    assert "psa" in {entry.company for entry in value.ranked}


def test_nothing_rankable_is_an_admission_rather_than_an_empty_comparison() -> None:
    """A comparison of nobody is not a comparison."""
    no_raw = tuple(company(slug, raw_price=None) for slug in CARDS)

    assert admission(rank(no_raw, strategy_for("roi"))).reason == "no_company_can_be_ranked"
    assert admission(rank((), strategy_for("roi"))).reason == "no_company_can_be_ranked"


def test_the_cost_and_probability_modes_survive_a_missing_market() -> None:
    """Neither reads a price, so neither goes undefined when nobody has one."""
    no_raw = tuple(company(slug, raw_price=None) for slug in CARDS)

    assert order(under("lowest_total_cost", no_raw)) == ["bgs", "tag", "psa"]
    assert order(under("highest_grade_probability", no_raw)) == ["tag", "psa", "bgs"]


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_each_ranked_figure_carries_the_confidence_of_the_figure_itself() -> None:
    """Not one opaque score: #64 gates on three independent sources.

    Distribution 0.8, every price 0.8, the raw price 0.6. The expectation is
    ``0.8 · (0.5·0.8 + 0.5·0.8) = 0.64``; the incremental figure is
    ``min(0.64, 0.6) = 0.6``; the probability mode carries the distribution's
    own 0.8; and a configured cost is a fact the user typed, not an estimate.
    """
    unsure = company(
        "psa",
        raw_price=raw("100.00", Confidence.of(0.6)),
        price_confidence=Confidence.of(0.8),
        distribution_confidence=Confidence.of(0.8),
    )

    def confidence_under(mode: str) -> float:
        return compared(rank([unsure], strategy_for(mode))).best.confidence.value

    assert confidence_under("expected_profit") == pytest.approx(0.6)
    assert confidence_under("expected_graded_value") == pytest.approx(0.64)
    assert confidence_under("highest_grade_probability") == pytest.approx(0.8)
    assert confidence_under("lowest_total_cost") == 1.0


# --------------------------------------------------------------------------
# The registry, and the sixth mode
# --------------------------------------------------------------------------


def test_the_registry_holds_exactly_the_five_modes_spec_43_names() -> None:
    assert set(STRATEGIES) == set(MODES)
    assert all(mode == strategy.mode for mode, strategy in STRATEGIES.items())


def test_the_registry_cannot_be_edited_at_runtime() -> None:
    with pytest.raises(TypeError):
        STRATEGIES["expected_profit"] = STRATEGIES["roi"]  # type: ignore[index]


def test_an_unknown_mode_is_refused_by_name() -> None:
    with pytest.raises(UnknownOptimizationMode, match="blended_score"):
        strategy_for("blended_score")


def test_a_sixth_mode_needs_no_change_to_the_engine() -> None:
    """§43: "The architecture must allow future modes."

    `rank` takes a strategy object, so this one is built at the call site and
    ranks without being registered, without a subclass, and without any edit to
    the five that already exist.

    Expected grade: PSA 9.5, TAG 9.8, BGS 0.7·9 + 0.2·9.5 + 0.1·10 = 9.2.
    """

    def expected_grade(candidate: CompanyOutlook) -> RankedCompany:
        total = sum(
            (
                Decimal(str(probability)) * grade.value
                for grade, probability in candidate.distribution.items()
            ),
            Decimal(0),
        )
        return RankedCompany(
            company=candidate.company,
            value=total,
            confidence=candidate.distribution_confidence,
            figure="expected_grade",
        )

    highest_expected_grade = OptimizationStrategy(
        mode="highest_expected_grade",
        label="Highest expected grade",
        higher_is_better=True,
        read=expected_grade,
    )

    result = compared(rank(three_companies(), highest_expected_grade))

    assert order(result) == ["tag", "psa", "bgs"]
    assert [entry.value for entry in result.ranked] == [
        Decimal("9.8"),
        Decimal("9.5"),
        Decimal("9.2"),
    ]
    assert "highest_expected_grade" not in STRATEGIES


# --------------------------------------------------------------------------
# What an outlook is, and what `rank` refuses
# --------------------------------------------------------------------------


def test_a_strategy_reads_the_figures_the_outlook_already_holds() -> None:
    """Nothing is recomputed — #62's rule, one layer up.

    Identity, not equality: a ratio that recomputed `_graded_proceeds` could
    drift from the profit figure it is a ratio of, and a strategy that
    recomputed a profit figure from the ladder could do the same.
    """
    candidate = company("psa")
    assert isinstance(candidate.incremental, IncrementalGradingDecision)
    assert isinstance(candidate.incremental_ratio, IncrementalRoi)
    assert isinstance(candidate.graded_proceeds, ExpectedValue)

    profit = compared(rank([candidate], strategy_for("expected_profit"))).best
    ratio = compared(rank([candidate], strategy_for("roi"))).best
    value = compared(rank([candidate], strategy_for("expected_graded_value"))).best

    assert profit.value is candidate.incremental.incremental_profit
    assert ratio.value is candidate.incremental_ratio.incremental_roi
    assert value.value is candidate.graded_proceeds.amount


def test_one_company_cannot_appear_twice_in_a_comparison() -> None:
    with pytest.raises(InvalidComparison, match="psa"):
        rank((company("psa"), company("psa")), strategy_for("expected_profit"))


def test_a_comparison_carries_the_mode_it_was_ranked_under() -> None:
    result = compared(under("lowest_total_cost"))

    assert result.mode == "lowest_total_cost"
    assert result.label == STRATEGIES["lowest_total_cost"].label
    assert result.best is result.ranked[0]


def test_a_comparison_never_speaks_44s_recommendation_vocabulary() -> None:
    """`grade | do_not_grade` and `recommended_*` are #64's, not this module's."""
    reserved = {"recommended_action", "recommended_company", "reason", "action"}

    assert reserved.isdisjoint(CompanyComparison.__dataclass_fields__)
    assert reserved.isdisjoint(RankedCompany.__dataclass_fields__)
