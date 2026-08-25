"""Spec §41's profit figures. Today: the incremental grading decision.

§41 defines two, and says outright that the distinction "must be implemented
rather than conflated":

* the **incremental grading decision** — what a collector who already owns the
  card is asking. The acquisition cost is sunk, and the real alternative is
  selling the card raw today.
* the **investment return** — what an investor who bought the card to grade is
  asking. #61 adds it here, beside this one, so that a reader can see in one
  file that the two share no numerator, no denominator and no intermediate.

`docs/adr/0007-roi-and-the-capital-at-risk-basis.md` fixes the arithmetic and
this module carries it out verbatim:

```text
graded_proceeds       = Σ_g P(g) · ( V(g) - sale_costs(V(g)) )
raw_opportunity_value = raw_market_value - sale_costs(raw_market_value)
incremental_profit    = graded_proceeds - raw_opportunity_value - grading_costs
```

**There is no acquisition cost in this module's incremental branch, and no ROI
anywhere in it.** The first is #61's and the second is #62's; a parameter here
for either is the conflation §41 forbids, and
`test_no_acquisition_cost_can_reach_this_figure` is what fails when one appears.

Two omissions are what this figure exists to prevent, and both bias every
recommendation toward *grade*:

**Forgetting the raw-sale opportunity value.** The comparison is not "graded
proceeds against grading costs" — it is "graded proceeds against what the card
would fetch raw today". A card already worth 100 raw has to clear that bar
before grading has earned anything.

**Netting the selling fee off only one branch.** A raw sale pays the fee too,
and the two fees differ because the two prices do. Comparing gross graded
proceeds against a net raw value reports 110.00 where ADR 0007 example 2 reports
84.00.
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
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice, expected_value

__all__ = ["IncrementalGradingDecision", "incremental_grading_decision"]

#: Nobody knows what the card is worth raw, so the alternative to grading cannot
#: be priced. Deliberately not the same reason as the expectation's
#: ``no_graded_price_available``: which side of the comparison went missing is
#: what a caller reports and what #64 gates on.
NO_RAW_PRICE = "no_raw_price_available"


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
            none is wanted here (#61).
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

    # ADR 0007 applies `sale_costs` "per outcome, inside the sum, never to the
    # expected value", and `SellingFee.on`'s cap makes that arithmetically
    # different from netting the fee off the expectation afterwards rather than
    # merely differently written. Netting each `V(g)` first satisfies the rule
    # literally, which is why `expected_value` grows no fee parameter. The cap
    # is also what keeps every netted price non-negative, so `GradedPrice`
    # accepts it.
    net = {
        grade: GradedPrice(price.value - costs.selling_fee.on(price.value), price.confidence)
        for grade, price in prices.items()
    }
    expectation = expected_value(distribution, net, distribution_confidence=distribution_confidence)
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
