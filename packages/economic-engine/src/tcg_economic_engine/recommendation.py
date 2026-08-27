"""Spec §44's recommendation: `grade`, `do_not_grade`, `insufficient_information`.

Every figure M5 computes exists to reach this one sentence. §44 fixes the inputs
— company, grade distribution, market values, costs, optimization mode,
confidence — and the four outputs, and adds one prohibition that shapes the whole
module: *"Do not force a recommendation when data quality is inadequate."*

**The admission is an action, not an absent result.** :func:`recommend` returns a
:class:`Recommendation` and never
:class:`~tcg_domain.confidence.InsufficientInformation`, which is the one place
in this package the wrapper is not used. §44 lists `insufficient_information`
among the three actions and still requires a `reason` and a `confidence` beside
it, so a caller that had to unwrap an admission to discover the action *was* the
admission would hold two spellings of one thing — and the reason and confidence
§44 asks for would have nowhere to live.

**The mode picks the company; the economics pick the action.** Whichever of §43's
modes ranked, `recommended_company` is the order's leader and
`recommended_action` is read off *that* company's spec §41 incremental grading
decision. Those are two questions and §44 asks them separately: "which grader?"
is the preference the user expressed, and #63 binds that no economics may be
blended back into `highest_grade_probability` or `lowest_total_cost`; "is grading
worth it at all?" is economic whatever the answer to the first was. It is also
what makes `insufficient_information` reachable in the ordinary case — the
cheapest company to submit to may be one nobody holds a raw price for, so the
mode answers and the economics cannot.

**The reason is the comparison that fired.** §50 forbids explanations unrelated
to model evidence, so a :class:`Reason` is not prose and not a template chosen by
the outcome: it is the figure's name, its value, and the threshold it was
measured against. There is no sentence here that could drift from the numbers,
because there is no sentence. #65 serialises the fields and #66 writes the copy.

**Three confidence sources, gated separately.** Image quality, the grading
model's confidence in its distribution, and the market prices'. §44 outputs one
`confidence` and this module reports `min` of the three, but each is carried as
its own field and each clears its own threshold — averaging them into one score
would let a pristine photograph rescue a model nobody measured. `min` rather than
a product: :func:`~tcg_economic_engine.expectation.expected_value` already
multiplies three numbers in ``[0, 1]`` and its own note flags that compounding as
uncalibrated, so a fourth factor would make a worse number, not a safer one.

The price's confidence is not separable at this layer — #59 folds it into the
expectation together with the distribution's — so
:attr:`RecommendationThresholds.minimum_figure_confidence` gates the expectation,
which gates both, and :attr:`~RecommendationThresholds.minimum_grade_confidence`
additionally gates the distribution alone. That is the explicit combination the
issue asks for, spelled with the numbers that actually exist.

**Nothing is recomputed.** Every figure is read off a
:class:`~tcg_economic_engine.strategies.CompanyOutlook` — #62's rule against a
ratio drifting from the profit it is a ratio of, and #63's against a strategy
rebuilding a figure, applied to the layer that speaks to the user.

There is no natural language here, no threshold buried in a branch, and nothing
that reaches into how the distribution was produced (spec §2.3).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from tcg_domain import Confidence, Money, Uncertain

from tcg_economic_engine.errors import InvalidRecommendationThresholds
from tcg_economic_engine.profit import IncrementalGradingDecision
from tcg_economic_engine.strategies import (
    NO_RANKABLE_COMPANY,
    CompanyComparison,
    CompanyOutlook,
    OptimizationStrategy,
    rank,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "Reason",
    "Recommendation",
    "RecommendationThresholds",
    "RecommendedAction",
    "recommend",
]

#: The photograph was not good enough to build a recommendation on. Spec §19's
#: gate lets `poor` through with a warning rather than stopping the pipeline, so
#: this is the stage that declines to advise on it.
POOR_IMAGE_QUALITY: Final = "image_quality_below_threshold"

#: The grading model's own confidence in its distribution is too low. Separate
#: from the one below on purpose — see this module's docstring.
LOW_GRADE_CONFIDENCE: Final = "grade_confidence_below_threshold"

#: The graded expectation is too weakly trusted to decide on, which folds the
#: prices' confidence together with the distribution's (#59).
LOW_FIGURE_CONFIDENCE: Final = "figure_confidence_below_threshold"

#: Too much of the distribution has no price. #59 excludes unpriced grades and
#: renormalises rather than valuing them at zero, and left "too little priced to
#: report" to this module; this is that judgement.
TOO_MUCH_UNPRICED: Final = "unpriced_probability_too_high"

#: §41's incremental figure could not be formed and said nothing about why. A
#: fallback that should not occur: both admissions #60 returns carry a reason.
NO_INCREMENTAL_FIGURE: Final = "incremental_figure_unavailable"

#: Grading clears the margin of safety.
PROFIT_CLEARS_MARGIN: Final = "profit_clears_margin"

#: Grading does not. **Includes a small positive profit**: the threshold is a
#: margin, not a sign test, and two dollars of expected edge on a sixty-dollar
#: submission is not a recommendation to post the card.
PROFIT_BELOW_MARGIN: Final = "profit_below_margin"


class RecommendedAction(StrEnum):
    """§44's three actions, and only these.

    A closed enum, deliberately unlike
    :attr:`~tcg_economic_engine.strategies.OptimizationStrategy.mode`: §43 says
    the architecture "must allow future modes" and §44 says nothing of the kind.
    A fourth action would be a fourth thing the UI must render and a fourth
    branch every caller must handle, so it earns a spec change rather than a
    string.
    """

    GRADE = "grade"
    DO_NOT_GRADE = "do_not_grade"
    INSUFFICIENT_INFORMATION = "insufficient_information"


@dataclass(frozen=True, slots=True)
class Reason:
    """Why the recommendation is what it is — spec §44's `reason`.

    Structured rather than prose, which is how §50's "never generate
    explanations unrelated to model evidence" is satisfied by construction: a
    reason is a figure, its value and the threshold it was measured against, so
    there is nothing here that *could* be unrelated to the evidence.

    Args:
        code: What fired, as a stable machine name. #66 keys its copy off this;
            a propagated admission wears the reason string it arrived with
            (``no_raw_price_available``, ``no_graded_price_available``) rather
            than one invented here.
        figure: What was measured — ``incremental_profit``,
            ``unpriced_probability``, ``distribution_confidence``,
            ``image_quality``, ``graded_expectation_confidence``, or
            ``ranked_companies``. The same vocabulary
            :attr:`~tcg_economic_engine.strategies.RankedCompany.figure` uses.
        value: The number measured, in its own type. `None` when there was no
            number — a propagated admission is the absence of a figure, not a
            figure with a bad value.
        threshold: What it was compared against, `None` on the same terms.
    """

    code: str
    figure: str
    value: Money | Confidence | float | None
    threshold: Money | Confidence | float | None

    def __str__(self) -> str:
        if self.value is None:
            return f"{self.code}: {self.figure}"
        return f"{self.code}: {self.figure} = {self.value} (threshold {self.threshold})"


@dataclass(frozen=True, slots=True)
class RecommendationThresholds:
    """Where the recommendation changes its mind — a product decision, written down.

    The issue requires these be "explicit, documented and configurable"; they are
    a frozen record with per-field defaults rather than constants in a branch, so
    a house that grades on any positive edge configures one field and changes no
    code. #65 owns putting them in the economic configuration; there is no table
    here.

    **Every one is a minimum, and meeting it exactly passes** — the comparisons
    are :meth:`~tcg_domain.confidence.Confidence.is_below` and ``>=``, never
    strict on the near side. `maximum_unpriced_probability` is the one ceiling
    and is likewise inclusive.

    **All five defaults are provisional pending calibration.** CLAUDE.md names
    probability calibration as a coverage gap and M7/M8 own the Brier-score work;
    until it lands these are judgement, chosen to make `insufficient_information`
    genuinely common rather than theoretical — which is the issue's own test of
    whether the gate is calibrated at all.

    Args:
        minimum_image_quality: How good the photographs must be. `0.50` sits
            below §19's `good` and above a `poor` verdict, so the gate fires on
            exactly the images the pipeline warned about and did not stop.
        minimum_grade_confidence: How far the grading company's model must trust
            its own distribution.
        minimum_figure_confidence: How far the graded expectation must be
            trusted. Deliberately the lowest of the three: #59's confidence is a
            product of three numbers in ``[0, 1]``, so inputs nobody would call
            weak — `0.8 · 0.8 · 0.95` — already read as 0.61.
        maximum_unpriced_probability: How much of the distribution may have no
            price. A quarter of the outcomes unvalued is the most that can be
            reported as an answer; #59 renormalises around them, which makes a
            thin ladder look confident if nobody checks the coverage.
        minimum_incremental_profit: What grading must be expected to clear. **A
            margin of safety, not a sign test** — the alternative recommends
            posting a card for two dollars of expected edge against a
            distribution that is metres wide.

    Raises:
        InvalidRecommendationThresholds: If a field is not of its type, or the
            unpriced ceiling falls outside ``[0, 1]``.
    """

    minimum_image_quality: Confidence = field(default_factory=lambda: Confidence(0.5))
    minimum_grade_confidence: Confidence = field(default_factory=lambda: Confidence(0.5))
    minimum_figure_confidence: Confidence = field(default_factory=lambda: Confidence(0.4))
    maximum_unpriced_probability: float = 0.25
    minimum_incremental_profit: Money = field(default_factory=lambda: Money.of("5.00"))

    def __post_init__(self) -> None:
        # A trust boundary once #65 reads these from configuration, so the types
        # are checked rather than assumed — the reason `tcg_domain.money._quantised`
        # is typed `object`. `Confidence` and `Money` validate their own contents.
        for name, expected in (
            ("minimum_image_quality", Confidence),
            ("minimum_grade_confidence", Confidence),
            ("minimum_figure_confidence", Confidence),
            ("minimum_incremental_profit", Money),
        ):
            if not isinstance(getattr(self, name), expected):
                raise InvalidRecommendationThresholds(
                    f"{name} must be a {expected.__name__}, "
                    f"got {type(getattr(self, name)).__name__}"
                )

        fraction: object = self.maximum_unpriced_probability
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            raise InvalidRecommendationThresholds(
                f"maximum_unpriced_probability must be a real number, got {type(fraction).__name__}"
            )
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise InvalidRecommendationThresholds(
                f"maximum_unpriced_probability must lie in [0, 1], got {fraction!r}"
            )
        object.__setattr__(self, "maximum_unpriced_probability", float(fraction))

    def __str__(self) -> str:
        return (
            f"image>={self.minimum_image_quality}, grade>={self.minimum_grade_confidence}, "
            f"figure>={self.minimum_figure_confidence}, "
            f"unpriced<={self.maximum_unpriced_probability}, "
            f"profit>={self.minimum_incremental_profit}"
        )


#: The thresholds in force when a caller does not supply its own.
DEFAULT_THRESHOLDS: Final = RecommendationThresholds()


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Spec §44's output, plus the evidence behind it.

    Args:
        recommended_action: §44's verdict. One of three, always present.
        recommended_company: Which grader to send it to, or `None` whenever the
            action is `insufficient_information`. **Naming a company beside "we
            cannot tell" is the forcing §44 forbids** — a UI shown both renders
            the company as the recommendation. The comparison is still carried,
            so §49's compare table is unaffected.
        reason: The gate that decided it — the first failure in the documented
            order, or the profit comparison when none failed.
        confidence: The weakest of the three sources below. §44's single
            `confidence` field; the sources beside it are what keep it from
            being opaque.
        image_quality: What §19's gate made of the photographs, as the caller
            supplied it. A `Confidence` and never a
            :class:`~tcg_domain.image_quality.QualityReport`: this package must
            not be able to reach the image pipeline, which
            ``tests/test_economic_engine_purity.py`` enforces.
        grade_confidence: The recommended company's confidence in its own grade
            distribution. `None` when no company was chosen.
        figure_confidence: How far the graded expectation behind the decision is
            trusted — the prices' confidence and the distribution's, as #59
            combines them. `None` when there is no figure.
        failed_gates: Every gate that fired, the decisive one first. §44 asks for
            one `reason`; this is what lets #66 add "and also…", so a user who
            retakes the photograph is not sent back into a second wall they were
            never told about. Empty on `grade` and on `do_not_grade`.
        comparison: §49's "Compare PSA / TAG / BGS" under the chosen mode, or its
            admission. Carried rather than recomputed — a caller that ranked
            again could get an order that disagrees with the company named here.
    """

    recommended_action: RecommendedAction
    recommended_company: str | None
    reason: Reason
    confidence: Confidence
    image_quality: Confidence
    grade_confidence: Confidence | None
    figure_confidence: Confidence | None
    failed_gates: tuple[Reason, ...]
    comparison: Uncertain[CompanyComparison]

    def __str__(self) -> str:
        named = "" if self.recommended_company is None else f" {self.recommended_company}"
        return f"{self.recommended_action}{named}: {self.reason} ({self.confidence})"


def _failed_gates(
    winner: CompanyOutlook,
    image_quality: Confidence,
    thresholds: RecommendationThresholds,
) -> Iterator[Reason]:
    """Every gate the recommendation fails, in the order they are asked.

    The order is the pipeline's own — image, then the grading model, then the
    market — so that when several fail the decisive one is the earliest thing
    that actually went wrong, which is the one a user can act on first.

    The figure's own admission is asked fourth and **stops the walk**: the two
    gates below it read `unpriced_probability` and `confidence` off a result
    that does not exist. There is nothing to report about a number nobody could
    compute beyond the reason it could not be computed.
    """
    if image_quality.is_below(thresholds.minimum_image_quality):
        yield Reason(
            POOR_IMAGE_QUALITY, "image_quality", image_quality, thresholds.minimum_image_quality
        )

    if winner.distribution_confidence.is_below(thresholds.minimum_grade_confidence):
        yield Reason(
            LOW_GRADE_CONFIDENCE,
            "distribution_confidence",
            winner.distribution_confidence,
            thresholds.minimum_grade_confidence,
        )

    figure = winner.incremental
    if not isinstance(figure, IncrementalGradingDecision):
        # #60's own string, unaltered — `no_raw_price_available` and
        # `no_graded_price_available` say which side of the comparison went
        # missing, and re-deciding that here would lose it.
        yield Reason(figure.reason or NO_INCREMENTAL_FIGURE, "incremental_profit", None, None)
        return

    if figure.unpriced_probability > thresholds.maximum_unpriced_probability:
        yield Reason(
            TOO_MUCH_UNPRICED,
            "unpriced_probability",
            figure.unpriced_probability,
            thresholds.maximum_unpriced_probability,
        )

    if figure.confidence.is_below(thresholds.minimum_figure_confidence):
        yield Reason(
            LOW_FIGURE_CONFIDENCE,
            "graded_expectation_confidence",
            figure.confidence,
            thresholds.minimum_figure_confidence,
        )


def recommend(
    outlooks: Iterable[CompanyOutlook],
    strategy: OptimizationStrategy,
    *,
    image_quality: Confidence,
    thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS,
) -> Recommendation:
    """Spec §44's recommendation, from figures #59 to #63 already computed.

    Args:
        outlooks: One :class:`~tcg_economic_engine.strategies.CompanyOutlook` per
            company — §44's company, grade distribution, market values and costs,
            run through every M5 figure once.
        strategy: §44's `optimization_mode`, as the strategy object
            :func:`~tcg_economic_engine.strategies.rank` takes. A sixth mode is
            built at the call site and needs no change here.
        image_quality: The third confidence source — what spec §19's gate made of
            the photographs, on a ``[0, 1]`` scale. Required and without a
            default, for the reason
            :func:`~tcg_economic_engine.expectation.expected_value` requires the
            distribution's: assuming a photograph nobody assessed was a good one
            is the fabrication §2.7 forbids.
        thresholds: Where the answer changes. Defaults to
            :data:`DEFAULT_THRESHOLDS`.

    Returns:
        A :class:`Recommendation`, always. See this module's docstring for why
        the admission is an action rather than a wrapper.

    Raises:
        InvalidComparison: If two outlooks name the same company —
            :func:`~tcg_economic_engine.strategies.rank`'s own guard.

    The gates, in order, each against its own threshold:

    ```text
    1  nothing could be ranked            → insufficient_information
    2  image_quality below minimum        → insufficient_information
    3  distribution confidence below       → insufficient_information
    4  no incremental figure at all        → insufficient_information
    5  too much of the ladder unpriced     → insufficient_information
    6  expectation confidence below        → insufficient_information
    7  incremental_profit >= margin        → grade
    8  otherwise                           → do_not_grade
    ```

    Gate 8 covers a **positive** profit that does not clear the margin, and that
    is `do_not_grade` rather than `insufficient_information`: the data supported
    an answer and the answer is that grading is not worth it. Keeping the
    admission for inadequate data alone is what makes it mean one thing.
    """
    candidates = tuple(outlooks)
    comparison = rank(candidates, strategy)

    if not isinstance(comparison, CompanyComparison):
        # Every candidate's figure for this mode was an admission. #63 keeps the
        # four individual reasons on `CompanyComparison.unranked`, which is
        # unreachable here by construction — there is no comparison.
        nothing_ranked = Reason(
            comparison.reason or NO_RANKABLE_COMPANY, "ranked_companies", None, None
        )
        return Recommendation(
            recommended_action=RecommendedAction.INSUFFICIENT_INFORMATION,
            recommended_company=None,
            reason=nothing_ranked,
            confidence=image_quality,
            image_quality=image_quality,
            grade_confidence=None,
            figure_confidence=None,
            failed_gates=(nothing_ranked,),
            comparison=comparison,
        )

    winner = next(one for one in candidates if one.company == comparison.best.company)
    figure = winner.incremental
    failed = tuple(_failed_gates(winner, image_quality, thresholds))

    sources = [image_quality, winner.distribution_confidence]
    figure_confidence = (
        figure.confidence if isinstance(figure, IncrementalGradingDecision) else None
    )
    if figure_confidence is not None:
        sources.append(figure_confidence)

    if isinstance(figure, IncrementalGradingDecision) and not failed:
        clears = figure.incremental_profit >= thresholds.minimum_incremental_profit
        action = RecommendedAction.GRADE if clears else RecommendedAction.DO_NOT_GRADE
        named = comparison.best.company
        reason = Reason(
            PROFIT_CLEARS_MARGIN if clears else PROFIT_BELOW_MARGIN,
            "incremental_profit",
            figure.incremental_profit,
            thresholds.minimum_incremental_profit,
        )
    else:
        # `failed` cannot be empty here: a figure that is not a decision always
        # yields its own admission from `_failed_gates`.
        action = RecommendedAction.INSUFFICIENT_INFORMATION
        named = None
        reason = failed[0]

    return Recommendation(
        recommended_action=action,
        recommended_company=named,
        reason=reason,
        confidence=min(sources),
        image_quality=image_quality,
        grade_confidence=winner.distribution_confidence,
        figure_confidence=figure_confidence,
        failed_gates=failed,
        comparison=comparison,
    )
