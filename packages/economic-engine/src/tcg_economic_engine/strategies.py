"""Spec §43's five optimization modes, and spec §49's "Compare PSA / TAG / BGS".

§43 fixes the five names and says the architecture "must allow future modes".
The point of having five is that they disagree: on one card, on one day, the
company that earns the most is not the company most likely to award a ten and is
not the cheapest to submit to. Letting the user say which of those they meant is
the feature; picking for them is not.

```text
expected_profit             incremental_profit            highest
roi                         incremental_roi               highest
highest_grade_probability   P(the distribution's top)     highest
lowest_total_cost           grading_costs (five items)    lowest
expected_graded_value       graded_proceeds               highest
```

**Two of the five are not economic objectives.** `highest_grade_probability`
ranks by the odds of the best outcome and can recommend a company that loses
money; `lowest_total_cost` ranks by what the submission costs and ignores what
comes back. Both are legitimate user preferences, and "improving" either by
blending profit back in deletes the preference the user expressed.

**`expected_profit` and `roi` always read the incremental pair.** §41 defines
two profit figures and ADR 0007 two ratios, and the collector's question — "I
own this card; is grading it worth it?" — is the ordinary one, the one #66 shows
by default, and the only one that is answerable without user input. The
investment figures are computed and carried on every
:class:`CompanyOutlook` for #64 to report; they simply never decide an order.
Choosing a denominator by whether a form field happened to be filled is exactly
the casual choice spec §42 forbids.

**`roi` is a mode name, and no figure here is ever called `roi`.** ADR 0007's
rule survives one layer up: what a candidate is ranked on is named
`incremental_roi` in :attr:`RankedCompany.figure`, so a comparison cannot be
rendered under a label its number does not match.

**Nothing is recomputed.** A :class:`CompanyOutlook` runs #59 to #62 once and
every strategy reads a field off it — the same arrangement #62 uses to keep a
ratio from drifting from the profit it is a ratio of, applied to the layer that
compares companies rather than figures.

**A mode is a `str`, never a closed enum**, for the reason
:class:`tcg_grading_companies.port.GradingCompany` is not closed: a mode nobody
wrote a strategy for is still a mode. :func:`rank` takes a strategy *object*, so
a sixth one is constructed at the call site and needs no edit here;
:data:`STRATEGIES` is only the lookup table for the five names §43 fixes.

There is no recommendation in this module. `grade | do_not_grade |
insufficient_information`, the reason and the thresholds are spec §44's, and #64
owns all of them — which is why nothing here is called `recommended_anything`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from tcg_domain import (
    Confidence,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    Money,
    Uncertain,
)

from tcg_economic_engine.costs import CostConfiguration
from tcg_economic_engine.errors import InvalidComparison, UnknownOptimizationMode
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice
from tcg_economic_engine.profit import (
    IncrementalGradingDecision,
    InvestmentReturn,
    _graded_proceeds,
    incremental_grading_decision,
    investment_return,
)
from tcg_economic_engine.roi import (
    IncrementalRoi,
    InvestmentRoi,
    incremental_roi,
    investment_roi,
)

__all__ = [
    "STRATEGIES",
    "CompanyComparison",
    "CompanyOutlook",
    "OptimizationStrategy",
    "RankedCompany",
    "company_outlook",
    "rank",
    "strategy_for",
]

#: Every candidate's figure for the selected mode was an admission, so there is
#: no order to report. Deliberately not one of the four reasons that produced it:
#: a caller reads those off :attr:`CompanyComparison.unranked`, and flattening
#: them into this string would lose which side of which figure went missing.
NO_RANKABLE_COMPANY = "no_company_can_be_ranked"

#: What a configured cost is worth as evidence. A line item is a number the user
#: typed, not an estimate anything discounted — the reason #61 types the
#: acquisition cost `Money` rather than `GradedPrice`.
CONFIGURED = Confidence(1.0)


def _sortable(value: Money | Decimal | float) -> Decimal:
    """One exact ordering key for the three shapes a ranked figure takes.

    Ranked values are money (profit, proceeds, costs), a four-place ratio, or a
    probability, and no two of them meet inside one mode. Normalising here keeps
    :func:`rank` from having to know which, and keeps the tie comparison exact:
    a probability goes through ``Decimal(str(p))``, the shortest round-tripping
    form :func:`~tcg_economic_engine.expectation.expected_value` already uses, so
    two models that emitted the same number tie and two that did not, do not.

    No currency check. :class:`~tcg_domain.money.Currency` has one member, #53
    owns the USD→SGD conversion, and CLAUDE.md binds that this package takes SGD
    and never converts.
    """
    if isinstance(value, Money):
        return value.amount
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class RankedCompany:
    """One company's standing under one mode.

    Args:
        company: The company's lowercase slug. A `str` and not
            :class:`tcg_grading_companies.port.GradingCompany`, because
            ``tests/test_economic_engine_purity.py`` allows this package to
            import `tcg_domain` and nothing else — the economics must not be
            able to reach a grading company at all.
        value: The figure this mode ranked on, in its own type. Money stays
            money so #65 can put a currency on the wire, a ratio stays a
            four-place `Decimal`, a probability stays a `float`.
        confidence: How far *that figure* is trusted — never an average of
            several. §44 hands #64 confidence as an input and its notes require
            the three sources be combined explicitly rather than blurred into
            one score, which is only possible if each figure arrives with its
            own.
        figure: What the number is — ``incremental_profit``, ``incremental_roi``,
            ``grading_costs``, ``graded_proceeds``, or ``P(10)``. Per candidate
            rather than per mode, because `highest_grade_probability` names a
            different grade for each company. #64 builds §44's reason from this;
            §50 forbids an explanation unrelated to the evidence, and a number
            whose name travels with it is what makes that possible.
    """

    company: str
    value: Money | Decimal | float
    confidence: Confidence
    figure: str

    def __str__(self) -> str:
        return f"{self.company}: {self.figure} = {self.value} ({self.confidence})"


@dataclass(frozen=True, slots=True)
class CompanyOutlook:
    """Everything the economics know about one company, computed once.

    Spec §44's inputs for one company, plus every figure #59 to #62 derive from
    them. Strategies read fields off this and **recompute nothing** — the same
    rule #62 applies to the ratios, at the layer that compares companies. A
    strategy that rebuilt a profit figure from the ladder could disagree with the
    one #64 reports beside it.

    Args:
        company: The company's lowercase slug.
        distribution: The retained grade distribution. Kept whole, because
            `highest_grade_probability` reads a term out of it and CLAUDE.md's
            central invariant is that the distribution *is* the value.
        distribution_confidence: How far the distribution itself is trusted.
        costs: The configuration every figure here was computed against.
        graded_proceeds: ``Σ P(g)·(V(g) - sale_costs(V(g)))``, computed
            independently of the two profit figures so that
            `expected_graded_value` still answers when no raw price is held and
            no acquisition cost was supplied. Three of the five modes need
            neither.
        incremental: Spec §41's incremental grading decision, or its admission.
        investment: Spec §41's investment return, or its admission — carried for
            #64 to report, never ranked on.
        incremental_ratio: ADR 0007's "return on grading", or its admission.
        investment_ratio: ADR 0007's "return on your investment", or its
            admission. Carried, never ranked on, for the reason in this module's
            docstring.
    """

    company: str
    distribution: GradeDistribution
    distribution_confidence: Confidence
    costs: CostConfiguration
    graded_proceeds: Uncertain[ExpectedValue]
    incremental: Uncertain[IncrementalGradingDecision]
    investment: Uncertain[InvestmentReturn]
    incremental_ratio: Uncertain[IncrementalRoi]
    investment_ratio: Uncertain[InvestmentRoi]

    def __str__(self) -> str:
        return f"{self.company}: {self.incremental}"


@dataclass(frozen=True, slots=True)
class OptimizationStrategy:
    """One answer to "what does best mean?".

    A dataclass with five instances rather than a `Protocol` with five classes:
    the modes differ in which field they read and which way they sort, and
    nothing else. `read` is the whole of a strategy.

    Args:
        mode: §43's name, or a future one. Free-form `str` — see the module
            docstring for why it is not an enum.
        label: What to call the objective on screen. #66's copy may replace it;
            it is here so a comparison can be rendered without a second lookup.
        higher_is_better: Whether the best value is the largest.
            `lowest_total_cost` is the one mode for which it is not.
        read: The figure this mode ranks on, or the admission that there is
            none. Returning
            :class:`~tcg_domain.confidence.InsufficientInformation` is how a
            company leaves the ranking entirely rather than sorting last — see
            :func:`rank`.
    """

    mode: str
    label: str
    higher_is_better: bool
    read: Callable[[CompanyOutlook], Uncertain[RankedCompany]]

    def __str__(self) -> str:
        return self.mode


@dataclass(frozen=True, slots=True)
class CompanyComparison:
    """Spec §49's "Compare PSA / TAG / BGS", under one mode.

    Deliberately carries no `recommended_action`, no `recommended_company` and
    no `reason`: that is §44's vocabulary and #64 owns all of it. This is an
    order and the figures that produced it, which is what a recommendation is
    built *from*.

    Args:
        mode: The mode this order is under. Two comparisons of the same card
            under different modes are different answers, not inconsistent ones.
        label: The strategy's label, carried so a caller need not look it up.
        ranked: The companies with a figure, best first.
        unranked: The companies without one, by slug, each wearing its own
            reason. Sorted, so two identical analyses serialise identically.
        tied_at_the_top: The companies sharing the best value, or empty when the
            leader is alone. **Present so nobody reports a coin-flip as a
            verdict**: the order among them was decided alphabetically and means
            nothing.
    """

    mode: str
    label: str
    ranked: tuple[RankedCompany, ...]
    unranked: Mapping[str, InsufficientInformation]
    tied_at_the_top: tuple[str, ...]

    @property
    def best(self) -> RankedCompany:
        """The leading company. Never absent: an empty order is an admission."""
        return self.ranked[0]

    def __str__(self) -> str:
        return f"{self.mode}: {', '.join(entry.company for entry in self.ranked)}"


def company_outlook(
    company: str,
    distribution: GradeDistribution,
    prices: Mapping[Grade, GradedPrice],
    raw_price: GradedPrice | None,
    acquisition_cost: Money | None,
    costs: CostConfiguration,
    *,
    distribution_confidence: Confidence,
) -> CompanyOutlook:
    """Run every M5 figure for one company, once.

    Args:
        company: The company's lowercase slug.
        distribution: That company's retained grade distribution. Each company
            has its own model and its own scale (spec §2.3, §22), so this is one
            company's distribution and never a shared one.
        prices: The **gross** graded market value of each grade, as
            :func:`~tcg_economic_engine.profit.incremental_grading_decision`
            takes them. The selling fee is netted off per outcome downstream.
        raw_price: What the card fetches ungraded, or `None` when no raw price is
            held — an admission, never a zero.
        acquisition_cost: What the user paid, or `None` when they did not say.
            Reaches only the investment figures, which nothing ranks on.
        costs: Spec §46's line items **for this company**. They differ per
            company, which is what `lowest_total_cost` exists to compare.
        distribution_confidence: How far this company's model is trusted.

    Raises:
        InvalidAcquisitionCost: If a cost was supplied and cannot be read.
        CurrencyMismatch: If the ladder, the raw price or the acquisition cost
            disagree on currency.

    ponytail: `_graded_proceeds` runs three times per company — once here and
    once inside each profit figure. It is pure and the ladder is 55 entries at
    the very most, so the cost is noise beside the rest of an analysis; the
    upgrade, if a profile ever says otherwise, is for the two profit functions to
    accept a precomputed expectation, and #61 binds that the netting rule stays
    in one function whichever way that goes.
    """
    incremental = incremental_grading_decision(
        distribution, prices, raw_price, costs, distribution_confidence=distribution_confidence
    )
    investment = investment_return(
        distribution,
        prices,
        acquisition_cost,
        costs,
        distribution_confidence=distribution_confidence,
    )
    return CompanyOutlook(
        company=company,
        distribution=distribution,
        distribution_confidence=distribution_confidence,
        costs=costs,
        graded_proceeds=_graded_proceeds(distribution, prices, costs, distribution_confidence),
        incremental=incremental,
        investment=investment,
        incremental_ratio=incremental_roi(incremental),
        investment_ratio=investment_roi(investment),
    )


# --------------------------------------------------------------------------
# The five readers. Each is independently testable and knows one field.
# --------------------------------------------------------------------------


def _expected_profit(outlook: CompanyOutlook) -> Uncertain[RankedCompany]:
    """Spec §41's incremental grading decision — see the module docstring."""
    figure = outlook.incremental
    if not isinstance(figure, IncrementalGradingDecision):
        return figure
    return RankedCompany(
        company=outlook.company,
        value=figure.incremental_profit,
        confidence=figure.confidence,
        figure="incremental_profit",
    )


def _return_on_grading(outlook: CompanyOutlook) -> Uncertain[RankedCompany]:
    """ADR 0007's incremental ratio. The mode is `roi`; the figure never is."""
    ratio = outlook.incremental_ratio
    if not isinstance(ratio, IncrementalRoi):
        return ratio
    return RankedCompany(
        company=outlook.company,
        value=ratio.incremental_roi,
        confidence=ratio.confidence,
        figure="incremental_roi",
    )


def _highest_grade_probability(outlook: CompanyOutlook) -> RankedCompany:
    """The odds of the best outcome this company's model admits.

    The top grade is the **highest grade the distribution names**, not
    :attr:`~tcg_domain.distribution.GradeDistribution.most_likely_grade` and not
    the top of the company's scale — this package cannot see a scale, and asking
    for one would be the economics reaching into the grading models.

    A distribution whose head is a bucket therefore reports ``P(9_or_higher)``,
    which is what it is. Carrying the grade in `figure` is what keeps that
    visible: a bare 0.7 ranked against another company's ``P(10)`` would compare
    two different questions silently.

    Never an admission: :class:`~tcg_domain.distribution.GradeDistribution`
    refuses to be empty, so there is always a top grade and always a probability
    — which is why this mode still answers when nobody holds a price.
    """
    top = max(outlook.distribution)
    return RankedCompany(
        company=outlook.company,
        value=outlook.distribution.probability_of(top),
        confidence=outlook.distribution_confidence,
        figure=f"P({top})",
    )


def _lowest_total_cost(outlook: CompanyOutlook) -> RankedCompany:
    """What committing to grade with this company costs — **five** line items.

    :attr:`~tcg_economic_engine.costs.CostConfiguration.grading_costs`, as it
    stands. No total is computed here and none is stored: #58 binds that costs
    are named line items and that nothing may add a `total_costs` field, because
    a §47 dimension attaches to one line rather than to all of them.

    The selling fee is not in it, for two reasons that agree. ADR 0007 excludes
    it from `CapitalAtRisk` because it is paid out of a sale that may not happen;
    and it is a function of the sale price, so including it would fold the
    outcome into the one mode that exists to ignore the outcome.

    The confidence is :data:`CONFIGURED`. A line item is a number the user typed
    rather than an estimate anything discounted — #61's reasoning for the
    acquisition cost, and the reason this is not `distribution_confidence`.
    """
    return RankedCompany(
        company=outlook.company,
        value=outlook.costs.grading_costs,
        confidence=CONFIGURED,
        figure="grading_costs",
    )


def _expected_graded_value(outlook: CompanyOutlook) -> Uncertain[RankedCompany]:
    """What the card is expected to be worth graded, **net of the selling fee**.

    ADR 0007's `graded_proceeds` — the fee applied per outcome, inside the sum —
    which is what the user actually receives and is the quantity both profit
    numerators are built on. A second, gross expectation beside it would be a
    second intermediate free to disagree with the figures shown next to it,
    which is the arrangement #61 and #62 exist to prevent.
    """
    expectation = outlook.graded_proceeds
    if not isinstance(expectation, ExpectedValue):
        return expectation
    return RankedCompany(
        company=outlook.company,
        value=expectation.amount,
        confidence=expectation.confidence,
        figure="graded_proceeds",
    )


EXPECTED_PROFIT = OptimizationStrategy(
    mode="expected_profit",
    label="Most profit",
    higher_is_better=True,
    read=_expected_profit,
)

RETURN_ON_GRADING = OptimizationStrategy(
    mode="roi",
    label="Best return on grading",
    higher_is_better=True,
    read=_return_on_grading,
)

HIGHEST_GRADE_PROBABILITY = OptimizationStrategy(
    mode="highest_grade_probability",
    label="Best odds of the top grade",
    higher_is_better=True,
    read=_highest_grade_probability,
)

LOWEST_TOTAL_COST = OptimizationStrategy(
    mode="lowest_total_cost",
    label="Cheapest to submit",
    higher_is_better=False,
    read=_lowest_total_cost,
)

EXPECTED_GRADED_VALUE = OptimizationStrategy(
    mode="expected_graded_value",
    label="Highest graded value",
    higher_is_better=True,
    read=_expected_graded_value,
)

#: The five modes spec §43 fixes, by name. A lookup table and **not** the set of
#: valid modes: :func:`rank` takes a strategy object, so a sixth one ranks
#: without ever appearing here. Read-only, so nothing can register a mode at
#: runtime and leave two callers disagreeing about what `roi` means.
STRATEGIES: Mapping[str, OptimizationStrategy] = MappingProxyType(
    {
        strategy.mode: strategy
        for strategy in (
            EXPECTED_PROFIT,
            RETURN_ON_GRADING,
            HIGHEST_GRADE_PROBABILITY,
            LOWEST_TOTAL_COST,
            EXPECTED_GRADED_VALUE,
        )
    }
)


def strategy_for(mode: str) -> OptimizationStrategy:
    """The strategy §43 names `mode`.

    Raises:
        UnknownOptimizationMode: If no strategy carries that name. A caller with
            a mode of its own passes the strategy to :func:`rank` directly and
            never comes through here.
    """
    try:
        return STRATEGIES[mode]
    except KeyError:
        raise UnknownOptimizationMode(
            f"no optimization strategy for {mode!r}; spec §43 names {', '.join(sorted(STRATEGIES))}"
        ) from None


def rank(
    outlooks: Iterable[CompanyOutlook],
    strategy: OptimizationStrategy,
) -> Uncertain[CompanyComparison]:
    """Order the companies under one mode (spec §49's "Compare PSA / TAG / BGS").

    Args:
        outlooks: One :class:`CompanyOutlook` per company. Three in V1; the count
            is not fixed, and a fourth company costs one adapter and no change
            here.
        strategy: What "best" means. A strategy **object**, not a mode name, so a
            future mode needs no registration — see :data:`STRATEGIES`.

    Returns:
        A :class:`CompanyComparison`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` with reason
        :data:`NO_RANKABLE_COMPANY` when no company has a figure for this mode.
        An empty order is never returned as a comparison.

    Raises:
        InvalidComparison: If two outlooks name the same company, which would
            render one company twice and make "best" meaningless.

    **A company whose figure is undefined is not ranked at all.** It goes to
    :attr:`~CompanyComparison.unranked` wearing its own reason and appears
    nowhere in the order. Sorting it last with a sentinel would read as "this is
    the worst company", which is a claim nobody computed — the issue calls this
    out by name for `roi`, where ADR 0007's `no_capital_at_risk` is a question
    with no answer rather than a poor return.

    **Ties break alphabetically by slug**, deterministically and for want of
    anything meaningful: breaking them on a second figure would fold economics
    into the two modes that exist to exclude it. The order among tied companies
    means nothing, and :attr:`~CompanyComparison.tied_at_the_top` says so.

    The sort is two stable passes rather than one composite key. Python's sort
    leaves equal elements in place, `reverse=True` included, so sorting by slug
    and then by value gives alphabetical ties in **both** directions. A single
    key of ``(value, company)`` reversed would reverse the tie order on the four
    descending modes, which is a bug that only shows up on a tie.
    """
    ranked: list[RankedCompany] = []
    unranked: dict[str, InsufficientInformation] = {}
    for outlook in outlooks:
        if outlook.company in unranked or any(entry.company == outlook.company for entry in ranked):
            raise InvalidComparison(
                f"{outlook.company!r} appears twice in one comparison; "
                "a company is compared against the others, not against itself"
            )
        entry = strategy.read(outlook)
        if isinstance(entry, InsufficientInformation):
            unranked[outlook.company] = entry
        else:
            ranked.append(entry)

    if not ranked:
        return InsufficientInformation(NO_RANKABLE_COMPANY)

    alphabetical = sorted(ranked, key=lambda entry: entry.company)
    ordered = sorted(
        alphabetical,
        key=lambda entry: _sortable(entry.value),
        reverse=strategy.higher_is_better,
    )
    best = _sortable(ordered[0].value)
    tied = tuple(entry.company for entry in ordered if _sortable(entry.value) == best)

    return CompanyComparison(
        mode=strategy.mode,
        label=strategy.label,
        ranked=tuple(ordered),
        unranked=MappingProxyType(dict(sorted(unranked.items()))),
        tied_at_the_top=tied if len(tied) > 1 else (),
    )
