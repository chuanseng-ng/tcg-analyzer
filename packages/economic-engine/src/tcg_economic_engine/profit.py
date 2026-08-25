"""Spec §41's two profit figures, and the reason there are two.

§41 defines both and says outright that the distinction "must be implemented
rather than conflated":

* the **incremental grading decision** — what a collector who already owns the
  card is asking. The acquisition cost is sunk, and the real alternative is
  selling the card raw today.
* the **investment return** — what an investor who bought the card to grade is
  asking. Its alternative was not buying the card at all, so the raw sale price
  never enters it and the acquisition cost always does.

They live in one file so that a reader can see in one place that they share no
numerator, no denominator and no field name. What they *do* share is ADR 0007's
`graded_proceeds`, which both numerators are defined in terms of — and it is a
shared formula in :func:`_graded_proceeds`, never a shared object: each call
builds its own frozen :class:`~tcg_economic_engine.expectation.ExpectedValue`.

`docs/adr/0007-roi-and-the-capital-at-risk-basis.md` fixes the arithmetic and
this module carries it out verbatim:

```text
graded_proceeds       = Σ_g P(g) · ( V(g) - sale_costs(V(g)) )

raw_opportunity_value = raw_market_value - sale_costs(raw_market_value)
incremental_profit    = graded_proceeds - raw_opportunity_value - grading_costs

investment_profit     = graded_proceeds - acquisition_cost - grading_costs
```

**There is no ROI anywhere in this module.** That is #62's, and ADR 0007 forbids
a figure called `roi` alone; a parameter or field for one here is the conflation
§41 forbids, and `test_no_acquisition_cost_can_reach_this_figure` and
`test_the_two_figures_share_no_field_and_no_intermediate` are what fail when one
appears.

Three omissions are what these figures exist to prevent, and the first two bias
every recommendation toward *grade*:

**Forgetting the raw-sale opportunity value.** The incremental comparison is not
"graded proceeds against grading costs" — it is "graded proceeds against what the
card would fetch raw today". A card already worth 100 raw has to clear that bar
before grading has earned anything.

**Netting the selling fee off only one branch.** A raw sale pays the fee too, and
the two fees differ because the two prices do. Comparing gross graded proceeds
against a net raw value reports 110.00 where ADR 0007 example 2 reports 84.00.

**Reading an absent acquisition cost as zero.** It turns "I don't remember what I
paid" into "it was free" and reports a profit the user never made. Spec §45
forbids inferring the value at all, so the investment figure is simply undefined
without one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tcg_domain import (
    Confidence,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    Money,
    Uncertain,
)

from tcg_economic_engine.costs import CostConfiguration
from tcg_economic_engine.errors import InvalidAcquisitionCost
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice, expected_value

__all__ = [
    "IncrementalGradingDecision",
    "InvestmentReturn",
    "incremental_grading_decision",
    "investment_return",
]

#: Nobody knows what the card is worth raw, so the alternative to grading cannot
#: be priced. Deliberately not the same reason as the expectation's
#: ``no_graded_price_available``: which side of the comparison went missing is
#: what a caller reports and what #64 gates on.
NO_RAW_PRICE = "no_raw_price_available"

#: The user did not say what they paid, and spec §45 forbids inferring it. ADR
#: 0007's own string, which #65 puts on the wire as ``investment_roi_reason``.
NO_ACQUISITION_COST = "acquisition_cost_not_supplied"


def _graded_proceeds(
    distribution: GradeDistribution,
    prices: Mapping[Grade, GradedPrice],
    costs: CostConfiguration,
    distribution_confidence: Confidence,
) -> Uncertain[ExpectedValue]:
    """ADR 0007's ``Σ_g P(g)·(V(g) - sale_costs(V(g)))``, for both figures.

    ADR 0007 applies `sale_costs` "per outcome, inside the sum, never to the
    expected value", and `SellingFee.on`'s cap makes that arithmetically
    different from netting the fee off the expectation afterwards rather than
    merely differently written. Netting each `V(g)` first satisfies the rule
    literally, which is why `expected_value` grows no fee parameter. The cap is
    also what keeps every netted price non-negative, so `GradedPrice` accepts it.

    Both profit figures are defined in terms of this quantity, so it lives in one
    function — a rule this load-bearing written out twice is a rule that rots in
    one of the two places. Nothing is cached and nothing is shared: each call
    returns its own frozen :class:`ExpectedValue`, which is what keeps the two
    figures free of a common intermediate.
    """
    net = {
        grade: GradedPrice(price.value - costs.selling_fee.on(price.value), price.confidence)
        for grade, price in prices.items()
    }
    return expected_value(distribution, net, distribution_confidence=distribution_confidence)


def _validated_acquisition_cost(value: object) -> Money:
    """Check what the user says they paid.

    Typed `object` rather than `Money` for the reason
    ``tcg_domain.money._quantised`` is: this is a trust boundary — spec §45 makes
    the acquisition cost user input, so it arrives from a form and from untyped
    callers — and a `Money` annotation would make the float rejection below
    unreachable *to the type checker* while it stays entirely reachable at
    runtime, which is the exact combination that gets a real guard deleted as
    dead code.

    `None` never reaches here: absence is a result, not an error.
    """
    if isinstance(value, float):
        raise InvalidAcquisitionCost(
            f"an acquisition cost must be Money, not float: {value!r}. "
            "Use Money.of('45.00') — binary floats cannot represent cents exactly."
        )
    if not isinstance(value, Money):
        raise InvalidAcquisitionCost(
            f"an acquisition cost must be Money, got {type(value).__name__}. "
            "None is how spec §45 spells 'not supplied'; a figure must carry a currency."
        )
    # Read off the `Decimal` rather than compared with `Money.zero(value.currency)`:
    # an amount in a currency this package cannot construct is the subtraction's
    # business, and the `CurrencyMismatch` it raises names the real problem where
    # an `InvalidMoney` from building a zero here would not.
    if value.amount < 0:
        raise InvalidAcquisitionCost(
            f"an acquisition cost must not be negative, got {value}. "
            "ADR 0007 requires CapitalAtRisk to be a sum of non-negative quantities."
        )
    return value


@dataclass(frozen=True, slots=True)
class IncrementalGradingDecision:
    """What grading is worth to someone who already owns the card.

    Every component is carried, not just the total. A user shown one number
    cannot tell whether the recommendation turns on a shipping estimate or on
    the gap between the raw and graded prices, and the issue asks for exactly
    that distinction.

    Args:
        incremental_profit: ADR 0007's term, verbatim —
            ``graded_proceeds - raw_opportunity_value - grading_costs``.
            **Negative is a real answer**: it means selling raw is the better
            move. Turning a figure into `grade | do_not_grade |
            insufficient_information` is #64's job, not this one's.
        confidence: The lesser of the graded expectation's confidence and the
            raw price's. See :func:`incremental_grading_decision`.
        graded_proceeds: ``Σ P(g)·(V(g) - sale_costs(V(g)))``, conditional on a
            priced grade occurring — the fee applied **inside** the sum.
        raw_market_value: What the card fetches ungraded, before fees.
        raw_selling_fee: What selling it raw costs, capped at the price.
        raw_opportunity_value: `raw_market_value` less `raw_selling_fee`. Never
            negative, which is what keeps ADR 0007's "neither denominator can be
            negative" true for #62.
        grading_costs: `CostConfiguration.grading_costs` — **five** line items,
            the selling fee deliberately not among them.
        costs: The configuration this was computed against. Frozen, and already
            the per-line breakdown, so nothing here re-spells the line items or
            computes a total of them.
        unpriced_grades: The grades the expectation could not value, ascending.
        unpriced_probability: How much of the distribution they carried.
    """

    incremental_profit: Money
    confidence: Confidence
    graded_proceeds: Money
    raw_market_value: Money
    raw_selling_fee: Money
    raw_opportunity_value: Money
    grading_costs: Money
    costs: CostConfiguration
    unpriced_grades: tuple[Grade, ...]
    unpriced_probability: float

    def __str__(self) -> str:
        return f"{self.incremental_profit} ({self.confidence})"


@dataclass(frozen=True, slots=True)
class InvestmentReturn:
    """What buying this card to grade it is expected to return.

    A separate type from :class:`IncrementalGradingDecision` rather than a flag
    on one shared result, and with no field name in common that could carry a
    figure: spec §41 says the two "must not be conflated", and the way conflation
    actually happens is a caller displaying one number under the other's label.

    Args:
        investment_profit: ADR 0007's term, verbatim —
            ``graded_proceeds - acquisition_cost - grading_costs``. **Negative is
            a real answer**: the card did not pay. `grade | do_not_grade |
            insufficient_information` is #64's.
        confidence: The graded expectation's own, undiscounted. See
            :func:`investment_return`.
        graded_proceeds: ``Σ P(g)·(V(g) - sale_costs(V(g)))``, conditional on a
            priced grade occurring — the fee applied **inside** the sum. The same
            formula the incremental figure uses, computed separately.
        acquisition_cost: What the user says they paid. Always present on a
            result: without one there is no result at all.
        grading_costs: `CostConfiguration.grading_costs` — **five** line items,
            the selling fee deliberately not among them. §41's "all costs" is
            these five plus the selling fee, and the fee is already netted out of
            `graded_proceeds`; adding it here would charge it twice.
        costs: The configuration this was computed against. Frozen, and already
            the per-line breakdown — so no `total_costs` field and no
            `CostLineItem`, both of which #58 forbids.
        unpriced_grades: The grades the expectation could not value, ascending.
        unpriced_probability: How much of the distribution they carried.
    """

    investment_profit: Money
    confidence: Confidence
    graded_proceeds: Money
    acquisition_cost: Money
    grading_costs: Money
    costs: CostConfiguration
    unpriced_grades: tuple[Grade, ...]
    unpriced_probability: float

    def __str__(self) -> str:
        return f"{self.investment_profit} ({self.confidence})"


def incremental_grading_decision(
    distribution: GradeDistribution,
    prices: Mapping[Grade, GradedPrice],
    raw_price: GradedPrice | None,
    costs: CostConfiguration,
    *,
    distribution_confidence: Confidence,
) -> Uncertain[IncrementalGradingDecision]:
    """Spec §41's incremental grading decision, on ADR 0007's basis.

    Args:
        distribution: The retained grade distribution for one grading company.
        prices: The **gross** graded market value of each grade. The selling fee
            is netted off here, per outcome, so a caller passes what the market
            says rather than pre-discounting anything.
        raw_price: What the card fetches ungraded, and how far that is trusted.
            `None` when no raw price is held — which is an admission, not a
            zero. Typed `GradedPrice | None` rather than `Money | None` for that
            reason: a raw price of ``0.00`` says the card is worthless, and
            passing it for "unknown" deletes the opportunity cost from the
            comparison, which is the one omission this figure exists to prevent.
            The type is the expectation's own price pair — value plus a
            confidence already discounted for age — which is the same shape
            ``GET /cards/{id}/market`` serves for ``raw``.
        costs: Spec §46's line items. Only `grading_costs` (five of the six) and
            `selling_fee` are read; there is no acquisition cost in it (#58) and
            none is wanted here — :func:`investment_return` is what takes one.
        distribution_confidence: How far the distribution itself is trusted.
            Required and without a default, for the reason
            :func:`~tcg_economic_engine.expectation.expected_value` requires it.

    Returns:
        An :class:`IncrementalGradingDecision`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` — the
        expectation's own ``no_graded_price_available`` propagated unchanged, or
        :data:`NO_RAW_PRICE` when the raw side cannot be priced.

    Raises:
        CurrencyMismatch: If the raw price and the graded ladder disagree. #53
            owns the USD→SGD conversion; this package takes SGD and never
            converts.

    The confidence is ``min(graded_expectation, raw_price)``. This figure is a
    *difference* between two quantities rather than one estimate refined by
    another, so it is no better than its weakest side: a fresh graded ladder
    cannot rescue a raw price nobody has seen in a month, and the reverse holds
    too. `min` is monotone in both, which is the property #64 needs.

    ponytail: `min` rather than a product, deliberately. Multiplying would imply
    the two are independent evidence for one quantity and would compound a third
    factor onto the two `expected_value` already multiplies — the compounding its
    own `ponytail:` comment flags as uncalibrated. Calibration is M7/M8's Brier
    work; when it lands, this is one line.
    """
    if raw_price is None:
        return InsufficientInformation(NO_RAW_PRICE)

    expectation = _graded_proceeds(distribution, prices, costs, distribution_confidence)
    if not isinstance(expectation, ExpectedValue):
        return expectation

    raw_selling_fee = costs.selling_fee.on(raw_price.value)
    raw_opportunity_value = raw_price.value - raw_selling_fee
    grading_costs = costs.grading_costs

    return IncrementalGradingDecision(
        incremental_profit=expectation.amount - raw_opportunity_value - grading_costs,
        confidence=min(expectation.confidence, raw_price.confidence),
        graded_proceeds=expectation.amount,
        raw_market_value=raw_price.value,
        raw_selling_fee=raw_selling_fee,
        raw_opportunity_value=raw_opportunity_value,
        grading_costs=grading_costs,
        costs=costs,
        unpriced_grades=expectation.unpriced_grades,
        unpriced_probability=expectation.unpriced_probability,
    )


def investment_return(
    distribution: GradeDistribution,
    prices: Mapping[Grade, GradedPrice],
    acquisition_cost: Money | None,
    costs: CostConfiguration,
    *,
    distribution_confidence: Confidence,
) -> Uncertain[InvestmentReturn]:
    """Spec §41's investment return: did buying this card to grade it pay?

    Args:
        distribution: The retained grade distribution for one grading company.
        prices: The **gross** graded market value of each grade, as
            :func:`incremental_grading_decision` takes them. The selling fee is
            netted off here, per outcome.
        acquisition_cost: What the user paid, or `None` when they did not say.
            Typed `Money | None` rather than `GradedPrice | None` — the
            deliberate contrast with the raw price above. A market price is an
            *estimate*, so it carries an age-discounted confidence; an
            acquisition cost is a **fact the user typed**, and attaching a
            confidence to it would invite something to discount it.
        costs: Spec §46's line items. `grading_costs` is five of the six; the
            sixth is already inside `graded_proceeds`. **The acquisition cost is
            not in here** (#58) and is passed separately, because §45 makes it
            optional user input while every line item has a default.
        distribution_confidence: How far the distribution itself is trusted, as
            above.

    Returns:
        An :class:`InvestmentReturn`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` —
        :data:`NO_ACQUISITION_COST` when the user did not supply one, or the
        expectation's own ``no_graded_price_available`` propagated unchanged.

    Raises:
        InvalidAcquisitionCost: If a cost was supplied and is not a non-negative
            `Money` — a `float` included.
        CurrencyMismatch: If the acquisition cost and the graded ladder
            disagree. #53 owns the USD→SGD conversion; this package takes SGD
            and never converts.

    **An absent acquisition cost makes the figure undefined, not zero.** Spec §45
    says the value is `null` and must not be inferred; ADR 0007 fixes what
    follows from that. Zero would report the whole of `graded_proceeds` less
    costs as profit on a card the user may have paid dearly for, and the raw
    market price would answer a question nobody asked — "what if you had bought
    it at today's price?" The incremental figure is unaffected either way: it
    never sees this number.

    The absence is answered **before** the ladder is read. Without an acquisition
    cost the question cannot be asked at all; without a ladder it could be asked
    but not answered, and the nearer admission is the more useful one.

    The confidence is the graded expectation's, undiscounted. There is no `min`
    here as there is on the incremental side, because there is no second
    *estimate*: the acquisition cost is a datum. That the two figures reach
    different confidences on the same ladder is the point rather than an
    inconsistency — the incremental one is additionally exposed to a raw price
    that may be stale.
    """
    if acquisition_cost is None:
        return InsufficientInformation(NO_ACQUISITION_COST)
    paid = _validated_acquisition_cost(acquisition_cost)

    expectation = _graded_proceeds(distribution, prices, costs, distribution_confidence)
    if not isinstance(expectation, ExpectedValue):
        return expectation

    grading_costs = costs.grading_costs

    return InvestmentReturn(
        investment_profit=expectation.amount - paid - grading_costs,
        confidence=expectation.confidence,
        graded_proceeds=expectation.amount,
        acquisition_cost=paid,
        grading_costs=grading_costs,
        costs=costs,
        unpriced_grades=expectation.unpriced_grades,
        unpriced_probability=expectation.unpriced_probability,
    )
