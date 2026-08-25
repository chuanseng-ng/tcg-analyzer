"""Spec §41's investment return — "I bought it to grade it; did it pay?".

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence. ADR 0007's worked examples 3 and 4 are reproduced verbatim; the rest
were computed on paper and written before the code they check.

Three of these are the acceptance criterion itself.
`test_an_absent_acquisition_cost_is_undefined_and_never_zero` fails if absence
is ever read as `0.00`, which turns "I don't remember what I paid" into "it was
free"; `test_supplying_an_acquisition_cost_changes_only_this_figure` is the
regression the issue names as mattering most; and
`test_the_two_figures_share_no_field_and_no_intermediate` fails if somebody
conflates the two figures spec §41 insists must stay apart.
"""

from __future__ import annotations

import dataclasses
import inspect
from decimal import Decimal

import pytest
from tcg_domain import (
    Confidence,
    CurrencyMismatch,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    Money,
)
from tcg_economic_engine import (
    CostConfiguration,
    GradedPrice,
    IncrementalGradingDecision,
    InvalidAcquisitionCost,
    InvestmentReturn,
    SellingFee,
    incremental_grading_decision,
    investment_return,
)
from tcg_economic_engine.costs import COMMITTED_LINE_ITEMS

CERTAIN = Confidence.of(1.0)

#: ADR 0007's cost line items: grading 40, outbound 12, return 8, the rest zero.
#: `grading_costs` is 60.00 — five items, the selling fee deliberately not among
#: them. Example 1 charges no selling fee; 2, 3 and 4 charge ten percent.
ADR_COSTS = CostConfiguration(
    grading_fee=Money.of("40.00"),
    outbound_shipping=Money.of("12.00"),
    return_shipping=Money.of("8.00"),
    insurance=Money.zero(),
    miscellaneous=Money.zero(),
    selling_fee=SellingFee(),
)
ADR_COSTS_WITH_FEE = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(rate=Decimal("0.10")))

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


def answer(result: object) -> InvestmentReturn:
    """Assert the result is a figure rather than an admission, and narrow it."""
    assert isinstance(result, InvestmentReturn), result
    return result


def owned(result: object) -> IncrementalGradingDecision:
    """The same, for the figure this one must never be confused with."""
    assert isinstance(result, IncrementalGradingDecision), result
    return result


# --------------------------------------------------------------------------
# ADR 0007's worked examples, verbatim
# --------------------------------------------------------------------------


def test_adr_0007_example_three_with_an_acquisition_cost_of_forty_five() -> None:
    """graded 234 - acquisition 45 - costs 60 = 129.00.

    `P(9) = 0.5, V(9) = 200`; `P(10) = 0.5, V(10) = 320`; a ten percent selling
    fee; grading 40, outbound 12, return 8. The same analysis reports an
    incremental profit of 84.00 — see
    `test_supplying_an_acquisition_cost_changes_only_this_figure`.
    """
    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("234.00")
    assert result.acquisition_cost == Money.of("45.00")
    assert result.grading_costs == Money.of("60.00")
    assert result.investment_profit == Money.of("129.00")


def test_adr_0007_example_four_an_absent_acquisition_cost_is_undefined() -> None:
    """`investment_profit = null`, reason `acquisition_cost_not_supplied`.

    Spec §45: the value is `null` and must not be inferred. ADR 0007 fixes what
    the figures derived from it become, and the reason string is its own.
    """
    result = investment_return(
        distribution(EVEN_SPLIT),
        prices(ADR_LADDER),
        None,
        ADR_COSTS_WITH_FEE,
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "acquisition_cost_not_supplied"


def test_adr_0007_example_one_costs_with_no_selling_fee() -> None:
    """graded 260 - acquisition 45 - costs 60 = 155.00."""
    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("45.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("260.00")
    assert result.investment_profit == Money.of("155.00")


# --------------------------------------------------------------------------
# The acceptance criterion
# --------------------------------------------------------------------------


def test_an_absent_acquisition_cost_is_undefined_and_never_zero() -> None:
    """`None` is an admission; `0.00` is a card that really was free.

    A raffle win, or a pack from a box somebody else paid for: zero is a real
    acquisition cost and reports 234 - 0 - 60 = 174.00. Reading absence as zero
    would report that same 174.00 for a card whose price the user simply does
    not recall, which is the substitution spec §45 forbids.
    """
    absent = investment_return(
        distribution(EVEN_SPLIT),
        prices(ADR_LADDER),
        None,
        ADR_COSTS_WITH_FEE,
        distribution_confidence=CERTAIN,
    )
    free = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.zero(),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert isinstance(absent, InsufficientInformation)
    assert free.acquisition_cost == Money.zero()
    assert free.investment_profit == Money.of("174.00")


def test_supplying_an_acquisition_cost_changes_only_this_figure() -> None:
    """The regression the issue names as mattering most.

    One card, one day, three questions. The acquisition cost moves the
    investment figure from 129.00 to 74.00 and leaves the incremental figure at
    ADR 0007 example 2's 84.00 both times — it is not an input to that one, and
    there is no parameter through which it could become one.
    """
    cheap = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )
    dear = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("100.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )
    collector = owned(
        incremental_grading_decision(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            GradedPrice(Money.of("100.00"), CERTAIN),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert cheap.investment_profit == Money.of("129.00")
    assert dear.investment_profit == Money.of("74.00")
    assert collector.incremental_profit == Money.of("84.00")


def test_the_two_figures_share_no_field_and_no_intermediate() -> None:
    """Spec §41 forbids conflating them, and a shared object is how that happens.

    Checked over `inspect.signature` and `dataclasses.fields` rather than over
    behaviour: a caller cannot display one figure under the other's label when
    the two result types share no name that could carry it. The one object the
    two results *do* share is the caller's own frozen `CostConfiguration`, which
    both were computed against and which is already the per-line breakdown.
    """
    incremental_fields = {field.name for field in dataclasses.fields(IncrementalGradingDecision)}
    investment_fields = {field.name for field in dataclasses.fields(InvestmentReturn)}
    parameters = set(inspect.signature(investment_return).parameters)

    assert "investment_profit" not in incremental_fields
    assert "incremental_profit" not in investment_fields
    # The raw-sale branch is the incremental figure's alone: an investor's
    # alternative was not buying the card, not selling it ungraded.
    assert not [name for name in parameters | investment_fields if name.startswith("raw")]
    # Both ROIs are #62's, and ADR 0007 forbids a figure called `roi` alone.
    assert not [name for name in parameters | investment_fields if "roi" in name]

    # A ladder with unpriced grades, so `unpriced_grades` is a real tuple on
    # both sides rather than the interned empty one.
    spread = {"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}
    ladder = {"9": "200.00", "8": "150.00"}
    investment = answer(
        investment_return(
            distribution(spread),
            prices(ladder),
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )
    collector = owned(
        incremental_grading_decision(
            distribution(spread),
            prices(ladder),
            GradedPrice(Money.of("100.00"), CERTAIN),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    shared = {
        name
        for name in incremental_fields & investment_fields
        if getattr(investment, name) is getattr(collector, name)
    }
    assert shared == {"costs"}
    assert investment.costs is ADR_COSTS_WITH_FEE


# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


def test_the_fee_is_charged_inside_the_sum_not_on_the_expectation() -> None:
    """A flat 100 fee on `V(9) = 50` and `V(10) = 300`, evenly split.

    Inside the sum:  0.5·(50-50) + 0.5·(300-100) = 0 + 100 = 100.00 → 20.00.
    Hoisted out:     fee.on(0.5·50 + 0.5·300) = 175 - 100 = 75.00 → -5.00.

    ADR 0007 requires the first and forecloses the second, for this figure as
    much as for #60's. `SellingFee.on` caps the fee at the sale price, which is
    what makes the two differ rather than merely read differently.
    """
    costs = dataclasses.replace(ADR_COSTS, selling_fee=SellingFee(flat=Money.of("100.00")))

    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices({"9": "50.00", "10": "300.00"}),
            Money.of("20.00"),
            costs,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("100.00")
    assert result.investment_profit == Money.of("20.00")
    assert result.investment_profit != Money.of("-5.00")


def test_the_breakdown_sums_to_the_total() -> None:
    """A user shown one number cannot tell which component moved it."""
    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert (
        result.graded_proceeds - result.acquisition_cost - result.grading_costs
        == result.investment_profit
    )

    committed = Money.zero()
    for name in COMMITTED_LINE_ITEMS:
        committed = committed + getattr(result.costs, name)
    assert committed == result.grading_costs
    assert result.costs is ADR_COSTS_WITH_FEE


def test_a_loss_is_a_figure_not_an_error() -> None:
    """Paid 400 for a card whose graded proceeds are 234: 234 - 400 - 60 = -226.00.

    "It did not pay" is an answer. Turning a figure into `grade | do_not_grade |
    insufficient_information` is #64's job, not this one's.
    """
    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("400.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.investment_profit == Money.of("-226.00")


def test_an_unpriced_grade_is_carried_through_rather_than_valued_at_zero() -> None:
    """Spec §24's example ladder, priced only at 9 and 8, with no selling fee.

    (0.69·200 + 0.17·150) / 0.86 = 163.5 / 0.86 = 190.116… → 190.12, then
    190.12 - 45.00 - 60.00 = 85.12. `10` and `7_or_lower` went unvalued and say
    so; neither was priced at nothing.
    """
    result = answer(
        investment_return(
            distribution({"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}),
            prices({"9": "200.00", "8": "150.00"}),
            Money.of("45.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.graded_proceeds == Money.of("190.12")
    assert result.investment_profit == Money.of("85.12")
    assert result.unpriced_grades == (Grade.parse("7_or_lower"), Grade.parse("10"))
    assert result.unpriced_probability == pytest.approx(0.14)


def test_no_graded_price_is_insufficient_information() -> None:
    """The expectation's own admission, propagated unchanged.

    Deliberately a different reason from `acquisition_cost_not_supplied`: which
    input went missing is what a caller reports and what #64 gates on.
    """
    result = investment_return(
        distribution({"9": 1.0}),
        prices({"8": "150.00"}),
        Money.of("45.00"),
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_graded_price_available"


def test_an_absent_acquisition_cost_answers_before_the_ladder_is_read() -> None:
    """Both inputs missing reports the acquisition cost, not the ladder.

    Without one the question cannot be asked at all; without a ladder it could
    be asked but not answered. The nearer admission is the more useful one.
    """
    result = investment_return(
        distribution({"9": 1.0}),
        prices({"8": "150.00"}),
        None,
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "acquisition_cost_not_supplied"


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_confidence_is_the_graded_expectations_alone() -> None:
    """An acquisition cost is a fact the user typed, so nothing discounts it.

    Prices at 0.5 and a certain distribution give 0.5. The incremental figure on
    the same ladder is additionally capped by a raw price nobody has seen in a
    month — 0.2 — because that side *is* an estimate. This one has no such side.
    """
    ladder = prices(ADR_LADDER, Confidence.of(0.5))

    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            ladder,
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )
    collector = owned(
        incremental_grading_decision(
            distribution(EVEN_SPLIT),
            ladder,
            GradedPrice(Money.of("100.00"), Confidence.of(0.2)),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == Confidence.of(0.5)
    assert collector.confidence == Confidence.of(0.2)


def test_an_untrusted_distribution_lowers_the_confidence() -> None:
    """0.6 · (0.5·1.0 + 0.5·1.0) = 0.6 — monotone in the model's own confidence."""
    result = answer(
        investment_return(
            distribution(EVEN_SPLIT),
            prices(ADR_LADDER),
            Money.of("45.00"),
            ADR_COSTS_WITH_FEE,
            distribution_confidence=Confidence.of(0.6),
        )
    )

    assert result.confidence == Confidence.of(0.6)


# --------------------------------------------------------------------------
# What an acquisition cost may be
# --------------------------------------------------------------------------


def call(acquisition_cost: object) -> object:
    """The figure, with everything but the acquisition cost held valid."""
    return investment_return(
        distribution({"9": 1.0}),
        prices({"9": "200.00"}),
        acquisition_cost,  # type: ignore[arg-type]
        ADR_COSTS,
        distribution_confidence=CERTAIN,
    )


def test_a_float_acquisition_cost_is_refused() -> None:
    """The rule `Money` already sets: 45.1 is not exactly 45.1."""
    with pytest.raises(InvalidAcquisitionCost):
        call(45.1)


def test_a_bare_decimal_acquisition_cost_is_refused() -> None:
    """A `Decimal` carries no currency, and #53 owns the conversion."""
    with pytest.raises(InvalidAcquisitionCost):
        call(Decimal("45.00"))


def test_a_negative_acquisition_cost_is_refused() -> None:
    """ADR 0007: neither denominator is negative, "sums of non-negative quantities".

    `CapitalAtRisk_inv` is `acquisition_cost + grading_costs`, so this guard is
    what keeps that claim true before #62 ever divides by it — the same job
    `SellingFee.on`'s cap does on the incremental side.
    """
    with pytest.raises(InvalidAcquisitionCost):
        call(Money.of("-1.00"))


def test_an_acquisition_cost_in_another_currency_is_refused() -> None:
    """The engine takes SGD and never converts — #53 owns the conversion.

    `Currency` has one member in V1, so a second cannot be built through the
    public API; it is forced onto a validated amount the way
    `test_domain_money.py` does. This is the test #60 could not write: there the
    selling fee reached the raw price first and raised the wrong failure, while
    an acquisition cost pays no fee and reaches `Money.__sub__` directly.
    """
    cost = Money.of("45.00")
    object.__setattr__(cost, "currency", "USD")

    with pytest.raises(CurrencyMismatch):
        call(cost)


# --------------------------------------------------------------------------
# The result
# --------------------------------------------------------------------------


def test_a_result_is_frozen() -> None:
    result = answer(
        investment_return(
            distribution({"9": 1.0}),
            prices({"9": "400.00"}),
            Money.of("45.00"),
            ADR_COSTS,
            distribution_confidence=CERTAIN,
        )
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.investment_profit = Money.zero()  # type: ignore[misc]
