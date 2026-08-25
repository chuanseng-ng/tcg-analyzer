"""Spec §41's incremental grading decision — "I own it; is grading worth it?".

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence. ADR 0007's worked examples 1 and 2 are reproduced verbatim; the rest
were computed on paper and written before the module they check.

Three of these are the acceptance criterion itself.
`test_no_acquisition_cost_can_reach_this_figure` fails if somebody threads a
sunk cost into the one figure spec §41 insists must not see one;
`test_the_raw_opportunity_value_is_net_of_the_selling_fee` fails if the raw
branch is left gross, which is the silent bias toward *grade*; and
`test_the_fee_is_charged_inside_the_sum_not_on_the_expectation` fails if
somebody hoists the fee out of the sum as a simplification.
"""

from __future__ import annotations

import dataclasses
import inspect
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
    SellingFee,
    incremental_grading_decision,
)
from tcg_economic_engine.costs import COMMITTED_LINE_ITEMS

CERTAIN = Confidence.of(1.0)

#: ADR 0007's cost line items: grading 40, outbound 12, return 8, the rest zero.
#: `grading_costs` is 60.00 — five items, the selling fee deliberately not among
#: them.
ADR_COSTS = CostConfiguration(
    grading_fee=Money.of("40.00"),
    outbound_shipping=Money.of("12.00"),
    return_shipping=Money.of("8.00"),
    insurance=Money.zero(),
    miscellaneous=Money.zero(),
    selling_fee=SellingFee(),
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


def answer(result: object) -> IncrementalGradingDecision:
    """Assert the result is a figure rather than an admission, and narrow it."""
    assert isinstance(result, IncrementalGradingDecision), result
    return result


# --------------------------------------------------------------------------
# ADR 0007's worked examples, verbatim
# --------------------------------------------------------------------------


def test_adr_0007_example_one_owned_card_no_selling_fee() -> None:
    """graded 260 - raw 100 - costs 60 = 100.00.

    `P(9) = 0.5, V(9) = 200`; `P(10) = 0.5, V(10) = 320`; raw 100; grading 40,
    outbound 12, return 8; selling fee 0.
    """
    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("260.00")
    assert result.raw_market_value == Money.of("100.00")
    assert result.raw_selling_fee == Money.zero()
    assert result.raw_opportunity_value == Money.of("100.00")
    assert result.grading_costs == Money.of("60.00")
    assert result.incremental_profit == Money.of("100.00")


def test_adr_0007_example_two_the_same_card_with_a_ten_percent_fee() -> None:
    """0.5·(200-20) + 0.5·(320-32) = 234; 100 - 10 = 90; 234 - 90 - 60 = 84.00."""
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(rate=Decimal("0.10")))

    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("234.00")
    assert result.raw_selling_fee == Money.of("10.00")
    assert result.raw_opportunity_value == Money.of("90.00")
    assert result.incremental_profit == Money.of("84.00")


# --------------------------------------------------------------------------
# The two components the issue says are most often got wrong
# --------------------------------------------------------------------------


def test_the_raw_opportunity_value_is_net_of_the_selling_fee() -> None:
    """Both branches pay the fee, and the fees differ because the prices do.

    On ADR 0007 example 2 the correct figure is 84.00. Charging the fee only to
    the graded side — gross raw 100 against net graded 234 — reports 74.00;
    comparing gross graded 260 against net raw 90 reports 110.00. The second is
    the systematic bias toward *grade* this test exists to prevent.
    """
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(rate=Decimal("0.10")))

    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.raw_opportunity_value == result.raw_market_value - result.raw_selling_fee
    assert result.raw_opportunity_value < result.raw_market_value
    assert result.incremental_profit == Money.of("84.00")
    assert result.incremental_profit != Money.of("110.00")


def test_the_fee_is_charged_inside_the_sum_not_on_the_expectation() -> None:
    """A flat 100 fee on `V(9) = 50` and `V(10) = 300`, evenly split.

    Inside the sum:  0.5·(50-50) + 0.5·(300-100) = 0 + 100 = 100.00.
    Hoisted out:     fee.on(0.5·50 + 0.5·300) = 175 - 100 =  75.00.

    ADR 0007 requires the first and forecloses the second. `SellingFee.on` caps
    the fee at the sale price, which is what makes the two differ — and what
    keeps the netted `V(9)` non-negative so `GradedPrice` still accepts it.
    """
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(flat=Money.of("100.00")))

    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "50.00", "10": "300.00"}),
            raw("20.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("100.00")
    assert result.graded_proceeds != Money.of("75.00")


def test_a_flat_fee_above_a_cheap_raw_price_leaves_nothing_rather_than_a_debt() -> None:
    """`fee.on(20)` is capped at 20, so the opportunity value is 0.00, never -80.

    ADR 0007 asserts that neither `CapitalAtRisk` denominator can be negative
    "because both are sums of non-negative quantities". `raw_opportunity_value`
    is one of the two terms of the incremental denominator, so #58's cap is what
    keeps that claim true here — before #62 ever divides by it.
    """
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(flat=Money.of("100.00")))

    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "50.00", "10": "300.00"}),
            raw("20.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.raw_selling_fee == Money.of("20.00")
    assert result.raw_opportunity_value == Money.zero()
    # 100.00 - 0.00 - 60.00
    assert result.incremental_profit == Money.of("40.00")


# --------------------------------------------------------------------------
# What the figure must not be able to see — the acceptance criterion
# --------------------------------------------------------------------------


def test_no_acquisition_cost_can_reach_this_figure() -> None:
    """Spec §41's whole point: the acquisition cost is sunk and is #61's.

    "Supplying an acquisition cost does not change this figure" is checked the
    only way that cannot rot — there is no parameter to supply it through, no
    field to read it from, and no ROI here either (#62 owns that).
    """
    parameters = set(inspect.signature(incremental_grading_decision).parameters)
    fields = {field.name for field in dataclasses.fields(IncrementalGradingDecision)}

    assert not [name for name in parameters | fields if "acquisition" in name]
    assert not [name for name in parameters | fields if "roi" in name]
    # `CostConfiguration` is the only object the figure reads costs from, and
    # #58 keeps the acquisition cost out of it.
    assert not [
        field.name for field in dataclasses.fields(CostConfiguration) if "acquisition" in field.name
    ]


def test_the_breakdown_sums_to_the_total() -> None:
    """A user shown one number cannot tell which component moved it."""
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(rate=Decimal("0.10")))

    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert (
        result.graded_proceeds - result.raw_opportunity_value - result.grading_costs
        == result.incremental_profit
    )
    assert result.raw_market_value - result.raw_selling_fee == result.raw_opportunity_value

    committed = Money.zero()
    for name in COMMITTED_LINE_ITEMS:
        committed = committed + getattr(result.costs, name)
    assert committed == result.grading_costs
    assert result.costs is costs


def test_grading_a_cheap_card_is_a_negative_figure_not_an_error() -> None:
    """`V(9) = 50`, raw 45, the default 100.00 of costs, 10% fee.

    graded 50 - 5 = 45.00; raw 45 - 4.50 = 40.50; 45 - 40.50 - 100 = -95.50.
    *Do not grade* is an answer, and #64 is what turns a figure into one.
    """
    result = answer(
        incremental_grading_decision(
            distribution({"9": 1.0}),
            prices({"9": "50.00"}),
            raw("45.00"),
            CostConfiguration(),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.grading_costs == Money.of("100.00")
    assert result.graded_proceeds == Money.of("45.00")
    assert result.raw_opportunity_value == Money.of("40.50")
    assert result.incremental_profit == Money.of("-95.50")


# --------------------------------------------------------------------------
# What it could not see
# --------------------------------------------------------------------------


def test_an_unpriced_grade_is_carried_through_rather_than_valued_at_zero() -> None:
    """Spec §24's example ladder, priced only at 9 and 8.

    (0.69·200 + 0.17·150) / 0.86 = 163.5 / 0.86 = 190.116… → 190.12, then
    190.12 - 100.00 - 60.00 = 30.12. `10` and `7_or_lower` went unvalued and
    say so; neither was priced at nothing.
    """
    result = answer(
        incremental_grading_decision(
            distribution({"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}),
            prices({"9": "200.00", "8": "150.00"}),
            raw("100.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("190.12")
    assert result.incremental_profit == Money.of("30.12")
    assert result.unpriced_grades == (Grade.parse("7_or_lower"), Grade.parse("10"))
    assert result.unpriced_probability == pytest.approx(0.14)


def test_no_graded_price_is_insufficient_information() -> None:
    """The expectation's own admission, propagated unchanged."""
    result = incremental_grading_decision(
        distribution({"9": 1.0}),
        prices({"8": "150.00"}),
        raw("100.00"),
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_graded_price_available"


def test_no_raw_price_is_insufficient_information() -> None:
    """Absence, not zero.

    A raw price of `0.00` says the card is worth nothing; `None` says nobody
    knows. Passing the first for the second removes the opportunity cost from
    the comparison entirely, which is the one omission the issue names.
    """
    result = incremental_grading_decision(
        distribution({"9": 0.5, "10": 0.5}),
        prices({"9": "200.00", "10": "320.00"}),
        None,
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_raw_price_available"


def test_a_raw_price_of_zero_is_a_price_and_not_a_missing_one() -> None:
    """260.00 - 0.00 - 60.00 = 200.00 — a free card is worth grading."""
    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("0.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.raw_opportunity_value == Money.zero()
    assert result.incremental_profit == Money.of("200.00")


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_confidence_is_no_higher_than_the_raw_price_confidence() -> None:
    """The figure is a difference; it is as good as its weakest input."""
    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00", Confidence.of(0.6)),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == Confidence.of(0.6)


def test_confidence_is_no_higher_than_the_graded_expectations() -> None:
    """And the other way round — `min`, not the raw price's alone."""
    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}, Confidence.of(0.5)),
            raw("100.00", Confidence.of(0.9)),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == Confidence.of(0.5)


def test_certainty_everywhere_is_certainty() -> None:
    """Three certain inputs do not compound into 0.61."""
    result = answer(
        incremental_grading_decision(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            raw("100.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == CERTAIN


# --------------------------------------------------------------------------
# The result
# --------------------------------------------------------------------------


def test_a_result_is_frozen() -> None:
    result = answer(
        incremental_grading_decision(
            distribution({"9": 1.0}),
            prices({"9": "400.00"}),
            raw("100.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.incremental_profit = Money.zero()  # type: ignore[misc]
