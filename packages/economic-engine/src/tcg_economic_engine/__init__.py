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

Everything re-exported below is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_economic_engine.costs import CostConfiguration, SellingFee
from tcg_economic_engine.errors import (
    EconomicEngineError,
    InvalidAcquisitionCost,
    InvalidCostConfiguration,
    InvalidGradedPrice,
)
from tcg_economic_engine.expectation import ExpectedValue, GradedPrice, expected_value
from tcg_economic_engine.profit import (
    IncrementalGradingDecision,
    InvestmentReturn,
    incremental_grading_decision,
    investment_return,
)

__all__ = [
    "CostConfiguration",
    "EconomicEngineError",
    "ExpectedValue",
    "GradedPrice",
    "IncrementalGradingDecision",
    "InvalidAcquisitionCost",
    "InvalidCostConfiguration",
    "InvalidGradedPrice",
    "InvestmentReturn",
    "SellingFee",
    "expected_value",
    "incremental_grading_decision",
    "investment_return",
]
