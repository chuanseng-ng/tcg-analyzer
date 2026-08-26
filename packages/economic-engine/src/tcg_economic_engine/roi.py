"""ADR 0007's two ROIs, and the reason there are two.

Spec §42 requires ROI to be defined in the domain documentation before it
reaches the UI, and says outright that *"the implementation must not casually
choose a denominator."* `docs/adr/0007-roi-and-the-capital-at-risk-basis.md` is
where it was chosen; this module carries that arithmetic out verbatim and
decides nothing:

```text
CapitalAtRisk_incr = raw_opportunity_value + grading_costs
incremental_roi    = incremental_profit / CapitalAtRisk_incr

CapitalAtRisk_inv  = acquisition_cost + grading_costs
investment_roi     = investment_profit / CapitalAtRisk_inv
```

**There are two ROIs and there is never one.** They sit under §41's two profit
figures, which "must be implemented rather than conflated", so a denominator
that served both would be exactly the conflation §41 forbids — and a *field*
called `roi` alone would be the same mistake one layer down, since a caller
cannot label a number it cannot tell apart. Both are separately named on the
wire, and :class:`IncrementalRoi` and :class:`InvestmentRoi` share no field. A
future single headline number is a new ADR, not a convenience.

Nothing is recomputed here. Both ratios read the profit figure #60 and #61
already produced — numerator and both denominator components are fields on it —
so a change to `_graded_proceeds` can never move a ratio without moving the
profit it is a ratio of. That is also why these take the profit figure's
`Uncertain` rather than a narrowed one: an unanswerable question is answered
once, where it arose, and travels back out wearing its own reason.

Three things this must not become, each of which reads as a simplification:

**A costs-only denominator.** "Return on the money you spend to grade" is the
number most tools report and it is the one ADR 0007 rejected by name: the
numerator has already subtracted the raw-sale opportunity value, so a
denominator omitting it pretends the card itself is not committed. On the ADR's
example 1 that reports 166.7% where the consistent basis reports 62.5%. The
smaller number is the correct one; `test_the_rejected_costs_only_basis_is_not_
what_we_report` is what fails if somebody "fixes" it.

**A zero denominator becoming infinity.** Nothing at risk is not an infinite
return; it is a question with no answer, and ADR 0007 makes it a `null` with a
stated reason while the profit figure is still reported.

**`Money`'s quantisation reused for a ratio.** Two places is for money. A ratio
is four, `ROUND_HALF_UP`, so that #65 can put `"0.5600"` on the wire as a string
and never route a decision figure through a binary float.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import ClassVar, Final

from tcg_domain import Confidence, InsufficientInformation, Money, Uncertain

from tcg_economic_engine.profit import IncrementalGradingDecision, InvestmentReturn

__all__ = [
    "IncrementalRoi",
    "InvestmentRoi",
    "incremental_roi",
    "investment_roi",
]

#: Nothing was committed, so there is nothing to have returned on. ADR 0007's
#: own string, and deliberately not an error: "you own a worthless card and
#: grading it is free" is a legitimate state of the world, not a caller's
#: mistake.
NO_CAPITAL_AT_RISK = "no_capital_at_risk"

#: A ratio is quantised to four places, never to :data:`~tcg_domain.money.CENTS`.
RATIO: Final = Decimal("0.0001")

#: The precision the division runs at. Fixed rather than inherited from the
#: caller's `decimal` context: `Decimal.__truediv__` reads thread-local state,
#: and a figure a user reads as a verdict must not depend on what some unrelated
#: code set. 28 is the module default, so this pins the ordinary behaviour
#: rather than choosing new behaviour.
DIVISION_PRECISION: Final = 28


def _ratio(profit: Money, capital: Money) -> Uncertain[Decimal]:
    """ADR 0007's ``profit / CapitalAtRisk``, for both figures.

    A shared *formula*, never a shared value — the same arrangement
    :func:`~tcg_economic_engine.profit._graded_proceeds` uses, and for the same
    reason: a rule this load-bearing written out twice is a rule that rots in
    one of the two places. Each caller builds its own denominator out of its own
    components and passes it in, so the two ratios still share no numerator, no
    denominator and no intermediate.

    The guard is ADR 0007's zero. `Money` quantises to the cent, so there is no
    near-zero band below ``0.01`` for a threshold to catch, and an exact
    `Decimal` division cannot produce an unbounded figure the way a float can —
    a denominator of one cent gives a large ratio that is simply true. "Too
    small a base to report meaningfully" is #64's judgement about the
    recommendation, the same call #59 left to it for thin price coverage, and
    inventing a floor here would amend an accepted ADR from the implementation.

    A negative denominator is unreachable: `SellingFee.on` caps the fee at the
    sale price so `raw_opportunity_value` cannot go below zero, and every cost
    line item and the acquisition cost are validated non-negative. It is folded
    into the same admission anyway, because silently inverting the sign of a
    decision figure is the one outcome worse than declining to produce it.

    No currency check is written. The denominator is built with `Money.__add__`,
    which raises :class:`~tcg_domain.errors.CurrencyMismatch` itself, and the
    numerator's currency was already fixed by the subtraction that produced it.
    """
    if capital.amount <= 0:
        return InsufficientInformation(NO_CAPITAL_AT_RISK)
    with localcontext() as context:
        context.prec = DIVISION_PRECISION
        return (profit.amount / capital.amount).quantize(RATIO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class IncrementalRoi:
    """What grading returns on what grading puts at risk.

    Args:
        incremental_roi: ``incremental_profit / (raw_opportunity_value +
            grading_costs)``, a four-place `Decimal`. **Negative is a real
            answer**: the card is worth more raw. `grade | do_not_grade |
            insufficient_information` is #64's job, not this one's.
        capital_at_risk: The denominator, carried so a user can see what the
            ratio is *of*. It includes the card, which is why this number is
            smaller than the one a competitor reports on identical inputs.
        confidence: The incremental figure's own — ``min`` of the graded
            expectation and the raw price. Dividing adds no estimate, so it
            discounts nothing.
    """

    #: ADR 0007's user-facing label. A `ClassVar` rather than a field with a
    #: default: a default can be overridden at construction, and the whole point
    #: is that nothing can display 0.5600 under the investor's label. Not a
    #: field, so it never reaches a serialiser as data a client could disagree
    #: with. #66 shows this figure by default — the collector who already owns
    #: the card is the ordinary case.
    label: ClassVar[str] = "Return on grading"

    incremental_roi: Decimal
    capital_at_risk: Money
    confidence: Confidence

    def __str__(self) -> str:
        return f"{self.label}: {self.incremental_roi} ({self.confidence})"


@dataclass(frozen=True, slots=True)
class InvestmentRoi:
    """What buying this card to grade it returns on what it cost to do so.

    A separate type from :class:`IncrementalRoi` with no field name in common,
    for the reason :class:`~tcg_economic_engine.profit.InvestmentReturn` is
    separate from :class:`~tcg_economic_engine.profit.IncrementalGradingDecision`:
    conflation happens when a caller displays one number under the other's
    label, and two names that cannot be swapped is what prevents it.

    Args:
        investment_roi: ``investment_profit / (acquisition_cost +
            grading_costs)``, a four-place `Decimal`. **Negative is a real
            answer**: the card did not pay.
        capital_at_risk: The denominator. The raw market price is not in it —
            an investor's alternative was not buying the card, not selling it
            ungraded.
        confidence: The investment figure's own — the graded expectation's,
            undiscounted, because an acquisition cost is a fact the user typed
            rather than a second estimate.
    """

    #: ADR 0007's user-facing label, on the same terms as
    #: :attr:`IncrementalRoi.label`. #66 shows this only once an acquisition
    #: cost has been entered.
    label: ClassVar[str] = "Return on your investment"

    investment_roi: Decimal
    capital_at_risk: Money
    confidence: Confidence

    def __str__(self) -> str:
        return f"{self.label}: {self.investment_roi} ({self.confidence})"


def incremental_roi(
    decision: Uncertain[IncrementalGradingDecision],
) -> Uncertain[IncrementalRoi]:
    """ADR 0007's "return on grading", from the figure #60 already computed.

    Args:
        decision: What :func:`~tcg_economic_engine.profit.incremental_grading_decision`
            returned, admission and all. Taking the `Uncertain` rather than a
            narrowed figure is what lets a caller write
            ``incremental_roi(incremental_grading_decision(...))`` and lets
            ``no_raw_price_available`` reach #64 wearing its own reason.

    Returns:
        An :class:`IncrementalRoi`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` — the decision's
        own reason propagated unchanged, or :data:`NO_CAPITAL_AT_RISK` when
        nothing is committed.

    **The denominator includes the card.** `raw_opportunity_value` is what
    selling it raw today would net, and it is committed the moment the card goes
    into an envelope; leaving it out is the costs-only basis the ADR rejected.
    Both components come off `decision`, so this ratio cannot drift from the
    profit it is a ratio of.
    """
    if not isinstance(decision, IncrementalGradingDecision):
        return decision

    capital_at_risk = decision.raw_opportunity_value + decision.grading_costs
    ratio = _ratio(decision.incremental_profit, capital_at_risk)
    if not isinstance(ratio, Decimal):
        return ratio

    return IncrementalRoi(
        incremental_roi=ratio,
        capital_at_risk=capital_at_risk,
        confidence=decision.confidence,
    )


def investment_roi(result: Uncertain[InvestmentReturn]) -> Uncertain[InvestmentRoi]:
    """ADR 0007's "return on your investment", from the figure #61 already computed.

    Args:
        result: What :func:`~tcg_economic_engine.profit.investment_return`
            returned, admission and all — including
            ``acquisition_cost_not_supplied``, which is how ADR 0007's example 4
            reaches the wire as ``investment_roi_reason`` without anything here
            re-deciding it.

    Returns:
        An :class:`InvestmentRoi`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` — the return's
        own reason propagated unchanged, or :data:`NO_CAPITAL_AT_RISK` when
        nothing is committed.

    **An absent acquisition cost never becomes zero here**, because it never
    reaches here: #61 answers the absence before the ladder is read, and this
    function passes that answer through. Zero would turn "I don't remember what
    I paid" into "it was free" and divide by the grading costs alone.

    `grading_costs` is five of spec §46's six line items. The selling fee is
    already netted out of `graded_proceeds` and is deliberately not in the
    denominator: it is paid out of proceeds rather than committed up front, and
    "completing" the sum to six breaks both ratios.
    """
    if not isinstance(result, InvestmentReturn):
        return result

    capital_at_risk = result.acquisition_cost + result.grading_costs
    ratio = _ratio(result.investment_profit, capital_at_risk)
    if not isinstance(ratio, Decimal):
        return ratio

    return InvestmentRoi(
        investment_roi=ratio,
        capital_at_risk=capital_at_risk,
        confidence=result.confidence,
    )
