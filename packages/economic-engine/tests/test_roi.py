"""ADR 0007's two ROIs — "return on grading" and "return on your investment".

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence. All five of ADR 0007's worked examples are reproduced verbatim —
CLAUDE.md records that regenerating them from the implementation voids that
criterion — and the rest were computed on paper and written before the module
they check.

Four of these are the acceptance criterion itself.
`test_the_rejected_costs_only_basis_is_not_what_we_report` fails if somebody
"simplifies" the denominator to the money spent grading, which is the basis the
ADR's Context section rejected by name and the one most competitors use;
`test_adr_0007_example_five_nothing_at_risk_is_undefined` fails if a zero
denominator ever produces an unbounded figure instead of an admission;
`test_neither_result_carries_the_other_figure_or_a_bare_roi` fails if the two
ratios are ever collapsed into one called `roi`; and
`test_the_label_travels_with_the_value_and_cannot_be_changed` fails if a caller
can put 0.5600 under "Return on your investment".
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from tcg_domain import (
    Confidence,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    Money,
)
from tcg_economic_engine import (
    CostConfiguration,
    GradedPrice,
    IncrementalGradingDecision,
    IncrementalRoi,
    InvestmentReturn,
    InvestmentRoi,
    SellingFee,
    incremental_grading_decision,
    incremental_roi,
    investment_return,
    investment_roi,
)
from tcg_economic_engine import (
    __all__ as PUBLIC_SURFACE,
)

CERTAIN = Confidence.of(1.0)

#: ADR 0007's cost line items: grading 40, outbound 12, return 8, the rest zero.
#: `grading_costs` is 60.00 — five items, the selling fee deliberately not among
#: them, which is what keeps it out of both denominators. Example 1 charges no
#: selling fee; 2, 3 and 4 charge ten percent.
ADR_COSTS = CostConfiguration(
    grading_fee=Money.of("40.00"),
    outbound_shipping=Money.of("12.00"),
    return_shipping=Money.of("8.00"),
    insurance=Money.zero(),
    miscellaneous=Money.zero(),
    selling_fee=SellingFee(),
)
ADR_COSTS_WITH_FEE = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(rate=Decimal("0.10")))

#: Every cost line item zero, for example 5's "nothing at risk".
NO_COSTS = CostConfiguration(
    grading_fee=Money.zero(),
    outbound_shipping=Money.zero(),
    return_shipping=Money.zero(),
    insurance=Money.zero(),
    miscellaneous=Money.zero(),
    selling_fee=SellingFee(),
)

#: ADR 0007's example card, and the ladder every example prices it against.
EVEN_SPLIT = {"9": 0.5, "10": 0.5}
ADR_LADDER = {"9": "200.00", "10": "320.00"}


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


def grading(
    raw_price: GradedPrice | None = None,
    costs: CostConfiguration = ADR_COSTS,
    ladder: dict[str, str] | None = None,
) -> object:
    """ADR 0007's card, priced for the collector who already owns it."""
    return incremental_grading_decision(
        distribution(EVEN_SPLIT),
        prices(ladder or ADR_LADDER),
        raw("100.00") if raw_price is None else raw_price,
        costs,
        distribution_confidence=CERTAIN,
    )


def investing(
    acquisition_cost: Money | None = None,
    costs: CostConfiguration = ADR_COSTS_WITH_FEE,
    ladder: dict[str, str] | None = None,
) -> object:
    """The same card, for the investor who bought it to grade."""
    return investment_return(
        distribution(EVEN_SPLIT),
        prices(ladder or ADR_LADDER),
        acquisition_cost,
        costs,
        distribution_confidence=CERTAIN,
    )


def on_grading(result: object) -> IncrementalRoi:
    """Assert the result is a ratio rather than an admission, and narrow it."""
    assert isinstance(result, IncrementalRoi), result
    return result


def on_investment(result: object) -> InvestmentRoi:
    """The same, for the ratio this one must never be confused with."""
    assert isinstance(result, InvestmentRoi), result
    return result


def owned(result: object) -> IncrementalGradingDecision:
    """Narrow the profit figure the incremental ratio is built from."""
    assert isinstance(result, IncrementalGradingDecision), result
    return result


def bought(result: object) -> InvestmentReturn:
    """The same, for the figure the investment ratio is built from."""
    assert isinstance(result, InvestmentReturn), result
    return result


def admission(result: object) -> InsufficientInformation:
    assert isinstance(result, InsufficientInformation), result
    return result


# --------------------------------------------------------------------------
# ADR 0007's worked examples, verbatim
# --------------------------------------------------------------------------


def test_adr_0007_example_one_owned_card_with_no_selling_fee() -> None:
    """profit 100 / (raw 100 + costs 60) = 0.6250.

    `P(9) = 0.5, V(9) = 200`; `P(10) = 0.5, V(10) = 320`; raw 100; grading 40,
    outbound 12, return 8; no selling fee. Graded proceeds 260.00, incremental
    profit 260 - 100 - 60 = 100.00, CapitalAtRisk 100 + 60 = 160.00.
    """
    result = on_grading(incremental_roi(grading()))

    assert result.capital_at_risk == Money.of("160.00")
    assert result.incremental_roi == Decimal("0.6250")


def test_adr_0007_example_two_the_same_card_with_a_ten_percent_selling_fee() -> None:
    """profit 84 / (raw 90 + costs 60) = 0.5600.

    Graded proceeds 0.5·(200-20) + 0.5·(320-32) = 234.00; the raw sale pays the
    fee too, so the opportunity value is 100 - 10 = 90.00; incremental profit
    234 - 90 - 60 = 84.00 over a CapitalAtRisk of 150.00.
    """
    result = on_grading(incremental_roi(grading(costs=ADR_COSTS_WITH_FEE)))

    assert result.capital_at_risk == Money.of("150.00")
    assert result.incremental_roi == Decimal("0.5600")


def test_adr_0007_example_three_both_figures_from_one_analysis() -> None:
    """investment 129 / (paid 45 + costs 60) = 1.2286, beside incremental 0.5600.

    `129 / 105 = 1.228571…`, so this is also the four-place `ROUND_HALF_UP`
    check. 0.5600 and 1.2286 describe the same card on the same day, which is
    the ADR's reason a response reporting either as "ROI" would be reporting a
    number the reader cannot interpret.
    """
    investment = on_investment(investment_roi(investing(Money.of("45.00"))))
    collector = on_grading(incremental_roi(grading(costs=ADR_COSTS_WITH_FEE)))

    assert investment.capital_at_risk == Money.of("105.00")
    assert investment.investment_roi == Decimal("1.2286")
    assert collector.incremental_roi == Decimal("0.5600")


def test_adr_0007_example_four_an_absent_acquisition_cost_is_undefined() -> None:
    """`investment_roi = null`, reason `acquisition_cost_not_supplied`.

    Never zero: that would turn "I don't remember what I paid" into "it was
    free" and report an infinite-looking return. The incremental figure is
    unaffected and still reports 0.5600 — it never sees the number.
    """
    investment = admission(investment_roi(investing(None)))
    collector = on_grading(incremental_roi(grading(costs=ADR_COSTS_WITH_FEE)))

    assert investment.reason == "acquisition_cost_not_supplied"
    assert not investment
    assert collector.incremental_roi == Decimal("0.5600")


def test_adr_0007_example_five_nothing_at_risk_is_undefined() -> None:
    """raw 0 and every line item 0 → `no_capital_at_risk`, never infinity.

    The profit figure is still reported: 260.00 - 0.00 - 0.00 = 260.00. It is
    only the ratio that has nothing to divide by, and the acceptance criterion
    is that it says so rather than producing an unbounded figure.
    """
    figure = owned(grading(raw_price=raw("0.00"), costs=NO_COSTS))
    assert figure.incremental_profit == Money.of("260.00")
    assert figure.raw_opportunity_value == Money.zero()
    assert figure.grading_costs == Money.zero()

    result = admission(incremental_roi(figure))

    assert result.reason == "no_capital_at_risk"
    assert not result


# --------------------------------------------------------------------------
# The denominator — the acceptance criterion
# --------------------------------------------------------------------------


def test_the_rejected_costs_only_basis_is_not_what_we_report() -> None:
    """0.6250 over 160.00, not 1.6667 over 60.00.

    ADR 0007 rejected "return on the money you spend to grade" by name: the
    numerator subtracts the raw-sale opportunity value, so a denominator that
    omits it pretends the card itself is not committed. On example 1's inputs
    the rejected basis reports 100 / 60 = 1.6667 — the number a user comparing
    against a competitor will see, and the reason the label has to carry the
    difference. This test is what fails if somebody "fixes" the small number.
    """
    result = on_grading(incremental_roi(grading()))

    assert result.capital_at_risk != Money.of("60.00")
    assert result.incremental_roi != Decimal("1.6667")
    assert result.capital_at_risk == Money.of("160.00")
    assert result.incremental_roi == Decimal("0.6250")


def test_each_denominator_is_its_own_two_components() -> None:
    """raw opportunity + grading costs; acquisition cost + grading costs.

    Checked against the profit figures' own fields rather than against a
    literal, so the two denominators cannot drift from the numerators they sit
    under. The selling fee is in neither: it is paid out of proceeds rather than
    committed up front, which is why `grading_costs` is five line items.
    """
    figure = owned(grading(costs=ADR_COSTS_WITH_FEE))
    invested = bought(investing(Money.of("45.00")))

    collector = on_grading(incremental_roi(figure))
    investor = on_investment(investment_roi(invested))

    assert collector.capital_at_risk == figure.raw_opportunity_value + figure.grading_costs
    assert investor.capital_at_risk == invested.acquisition_cost + invested.grading_costs
    # The fee was charged, and it is in neither denominator.
    assert figure.raw_selling_fee == Money.of("10.00")
    assert collector.capital_at_risk == Money.of("150.00")


def test_a_denominator_of_one_cent_is_a_figure_not_an_admission() -> None:
    """profit 259.99 / 0.01 = 25999.0000 — large, and correct.

    `Money` quantises to the cent, so there is no near-zero band below 0.01 and
    no unbounded float can arise from an exact `Decimal` division. The guard is
    the ADR's zero and only the ADR's zero; "too small a base to report
    meaningfully" is #64's judgement about the recommendation, not a threshold
    this function invents.
    """
    figure = owned(grading(raw_price=raw("0.01"), costs=NO_COSTS))

    result = on_grading(incremental_roi(figure))

    assert result.capital_at_risk == Money.of("0.01")
    assert result.incremental_roi == Decimal("25999.0000")


# --------------------------------------------------------------------------
# The two ratios are two — the acceptance criterion
# --------------------------------------------------------------------------


def test_neither_result_carries_the_other_figure_or_a_bare_roi() -> None:
    """ADR 0007 forbids a figure called `roi` alone, and a shared field is how.

    Checked over `dataclasses.fields` rather than over behaviour: a caller
    cannot display one ratio under the other's label when the two result types
    share no name that could carry it. A future single headline number is a new
    ADR, not a convenience.
    """
    grading_fields = {field.name for field in dataclasses.fields(IncrementalRoi)}
    investment_fields = {field.name for field in dataclasses.fields(InvestmentRoi)}

    assert "incremental_roi" in grading_fields
    assert "investment_roi" in investment_fields
    assert "investment_roi" not in grading_fields
    assert "incremental_roi" not in investment_fields
    assert "roi" not in grading_fields | investment_fields
    # Nor on the package surface, which is where #65 reads the names from.
    assert "roi" not in PUBLIC_SURFACE
    named = {name for name in PUBLIC_SURFACE if "roi" in name.lower()}
    assert named == {"IncrementalRoi", "InvestmentRoi", "incremental_roi", "investment_roi"}


def test_the_label_travels_with_the_value_and_cannot_be_changed() -> None:
    """ADR 0007's labels, verbatim, and unreachable from a caller.

    A `ClassVar` rather than a field with a default: a default can be
    overridden at construction, and the whole point is that nothing can put
    0.5600 under "Return on your investment".
    """
    collector = on_grading(incremental_roi(grading()))
    investor = on_investment(investment_roi(investing(Money.of("45.00"))))

    assert collector.label == "Return on grading"
    assert investor.label == "Return on your investment"
    assert IncrementalRoi.label != InvestmentRoi.label

    with pytest.raises(dataclasses.FrozenInstanceError):
        collector.incremental_roi = Decimal("1.6667")  # type: ignore[misc]
    # A frozen `slots=True` dataclass refuses the write either way; which
    # exception depends on whether the name is one of its fields, and `label`
    # deliberately is not.
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        collector.label = "Return on your investment"  # type: ignore[misc]
    # The class attribute is not a field, so it never reaches a serialiser as
    # data a client could disagree with.
    assert "label" not in {field.name for field in dataclasses.fields(IncrementalRoi)}


# --------------------------------------------------------------------------
# The ratio is a ratio
# --------------------------------------------------------------------------


def test_the_ratio_is_an_exact_decimal_quantised_to_four_places() -> None:
    """`Decimal("1.2286")`, never `1.228571428571…` and never a float.

    `Money`'s two-place quantisation is for money and does not apply to a
    ratio — four places is ADR 0007's, so that #65 can serialise `"0.5600"` as
    a string and never route a decision figure through a binary float.
    """
    investor = on_investment(investment_roi(investing(Money.of("45.00"))))
    collector = on_grading(incremental_roi(grading()))

    assert isinstance(investor.investment_roi, Decimal)
    assert not isinstance(investor.investment_roi, float)
    assert investor.investment_roi.as_tuple().exponent == -4
    assert collector.incremental_roi.as_tuple().exponent == -4
    # Four places is not two: a ratio quantised like money would report 1.23.
    assert str(investor.investment_roi) == "1.2286"
    assert str(collector.incremental_roi) == "0.6250"


def test_a_loss_is_a_figure_not_an_admission() -> None:
    """profit -38.00 / (raw 95 + costs 0) = -0.4000.

    A card worth more raw than graded is a real answer and the one the product
    exists to give. On a ladder of 60/60 with a five percent fee the graded
    proceeds are 60 - 3 = 57.00; the raw sale nets 100 - 5 = 95.00;
    57 - 95 - 0 = -38.00 over a CapitalAtRisk of 95.00. Turning that into
    `do_not_grade` is #64's.
    """
    figure = owned(
        grading(
            costs=dataclasses.replace(NO_COSTS, selling_fee=SellingFee(rate=Decimal("0.05"))),
            ladder={"9": "60.00", "10": "60.00"},
        )
    )
    assert figure.graded_proceeds == Money.of("57.00")
    assert figure.incremental_profit == Money.of("-38.00")

    result = on_grading(incremental_roi(figure))

    assert result.capital_at_risk == Money.of("95.00")
    assert result.incremental_roi == Decimal("-0.4000")


def test_the_confidence_is_the_profit_figures_own() -> None:
    """A ratio adds no estimate, so it discounts nothing.

    The incremental side is `min(expectation, raw price)` and the investment
    side is the expectation's alone — that difference is #60's and #61's, and
    dividing by a denominator the user typed or the market already priced must
    not move either.
    """
    figure = owned(grading(raw_price=raw("100.00", Confidence.of(0.4)), costs=ADR_COSTS_WITH_FEE))
    invested = bought(investing(Money.of("45.00")))

    assert on_grading(incremental_roi(figure)).confidence == figure.confidence
    assert on_grading(incremental_roi(figure)).confidence == Confidence.of(0.4)
    assert on_investment(investment_roi(invested)).confidence == invested.confidence


# --------------------------------------------------------------------------
# What comes in, and what goes back out unchanged
# --------------------------------------------------------------------------


def test_an_admission_passes_through_wearing_its_own_reason() -> None:
    """`no_raw_price_available` and `no_graded_price_available`, not relabelled.

    The ratio takes the profit figure's `Uncertain` rather than a narrowed one,
    so an unanswerable question is answered once, where it arose. Which side
    went missing is what #64 gates on, so nothing here may flatten the three
    reasons into one.
    """
    # Called directly: `grading`'s `raw_price=None` means "use the default",
    # and "nobody knows what it is worth raw" is exactly the case under test.
    nothing_raw = incremental_grading_decision(
        distribution(EVEN_SPLIT),
        prices(ADR_LADDER),
        None,
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )
    no_raw = admission(incremental_roi(nothing_raw))
    no_ladder = admission(incremental_roi(grading(ladder={"8": "10.00"})))
    no_cost = admission(investment_roi(investing(None)))

    assert no_raw.reason == "no_raw_price_available"
    assert no_ladder.reason == "no_graded_price_available"
    assert no_cost.reason == "acquisition_cost_not_supplied"
    assert len({no_raw.reason, no_ladder.reason, no_cost.reason, "no_capital_at_risk"}) == 4


def test_the_ratio_reads_the_figure_and_recomputes_nothing() -> None:
    """Both components come off the profit result, so the two cannot drift.

    `incremental_profit / (raw_opportunity_value + grading_costs)`, checked
    against the very fields the ratio was built from. If somebody ever
    recomputes `graded_proceeds` here instead, a change to `_graded_proceeds`
    would move one figure and not the other.
    """
    figure = owned(grading(costs=ADR_COSTS_WITH_FEE))

    result = on_grading(incremental_roi(figure))
    expected = (
        figure.incremental_profit.amount
        / (figure.raw_opportunity_value + figure.grading_costs).amount
    ).quantize(Decimal("0.0001"))

    assert result.incremental_roi == expected
