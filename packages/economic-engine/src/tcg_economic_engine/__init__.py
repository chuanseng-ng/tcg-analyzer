"""Grading economics: expected value, the two profit figures, ROI, strategies.

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
no change to this package. The recommendation itself — `grade | do_not_grade |
insufficient_information` — is spec §44's and belongs to #64.

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
    UnknownOptimizationMode,
)
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice, expected_value
from tcg_economic_engine.profit import (
    IncrementalGradingDecision,
    InvestmentReturn,
    incremental_grading_decision,
    investment_return,
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
    "InvestmentReturn",
    "InvestmentRoi",
    "OptimizationStrategy",
    "RankedCompany",
    "SellingFee",
    "UnknownOptimizationMode",
    "company_outlook",
    "expected_value",
    "incremental_grading_decision",
    "incremental_roi",
    "investment_return",
    "investment_roi",
    "rank",
    "strategy_for",
]
