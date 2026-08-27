"""Grading economics: expected value, profit, ROI, strategies, the recommendation.

**Grading is separate from economics** (CLAUDE.md's master architectural rule).
This package answers "is grading worth it?" from a grade distribution and a set
of prices it is handed, and must never depend on how either was produced. It
depends on `tcg-domain` and nothing else; `tests/test_economic_engine_purity.py`
enforces that.

`docs/adr/0007-roi-and-the-capital-at-risk-basis.md` is the authority on what
the figures mean. **There are two ROIs and never one**: `incremental_roi` for
the collector who already owns the card, `investment_roi` for the investor who
bought it to grade. They share no numerator, no denominator and no name.

Spec §43's five optimization modes live in `strategies`, and a mode is a `str`
rather than a closed enum: `rank` takes a strategy object, so a sixth mode needs
no change to this package.

Spec §44's recommendation — `grade | do_not_grade | insufficient_information` —
lives in `recommendation`, and is the one place here where the admission is an
**action** rather than an `Uncertain` wrapper: §44 requires a reason and a
confidence beside it, which an absent result has nowhere to put. The mode picks
the company and the economics pick the action; the reason is the figure, its
value and the threshold it failed, never prose.

Everything re-exported below is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_economic_engine.costs import CostConfiguration, SellingFee
from tcg_economic_engine.errors import (
    EconomicEngineError,
    InvalidAcquisitionCost,
    InvalidComparison,
    InvalidCostConfiguration,
    InvalidGradedPrice,
    InvalidRecommendationThresholds,
    UnknownOptimizationMode,
)
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice, expected_value
from tcg_economic_engine.profit import (
    IncrementalGradingDecision,
    InvestmentReturn,
    incremental_grading_decision,
    investment_return,
)
from tcg_economic_engine.recommendation import (
    DEFAULT_THRESHOLDS,
    Reason,
    Recommendation,
    RecommendationThresholds,
    RecommendedAction,
    recommend,
)
from tcg_economic_engine.roi import (
    IncrementalRoi,
    InvestmentRoi,
    incremental_roi,
    investment_roi,
)
from tcg_economic_engine.strategies import (
    STRATEGIES,
    CompanyComparison,
    CompanyOutlook,
    OptimizationStrategy,
    RankedCompany,
    company_outlook,
    rank,
    strategy_for,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "STRATEGIES",
    "CompanyComparison",
    "CompanyOutlook",
    "CostConfiguration",
    "EconomicEngineError",
    "ExpectedValue",
    "GradedPrice",
    "IncrementalGradingDecision",
    "IncrementalRoi",
    "InvalidAcquisitionCost",
    "InvalidComparison",
    "InvalidCostConfiguration",
    "InvalidGradedPrice",
    "InvalidRecommendationThresholds",
    "InvestmentReturn",
    "InvestmentRoi",
    "OptimizationStrategy",
    "RankedCompany",
    "Reason",
    "Recommendation",
    "RecommendationThresholds",
    "RecommendedAction",
    "SellingFee",
    "UnknownOptimizationMode",
    "company_outlook",
    "expected_value",
    "incremental_grading_decision",
    "incremental_roi",
    "investment_return",
    "investment_roi",
    "rank",
    "recommend",
    "strategy_for",
]
