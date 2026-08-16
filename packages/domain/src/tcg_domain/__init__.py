"""Framework-free core domain types for TCG Grading Advisor.

This package has **zero framework, database or provider dependencies** and must
keep them: it is imported by the API, by the analysis service and by every ML
module, so the invariants below exist exactly once and cannot drift
(see ``docs/adr/0001-language-boundaries-in-the-monorepo.md``).

Everything re-exported here is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_domain.card import ENGLISH, JAPANESE, POKEMON, CardReference
from tcg_domain.confidence import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    InsufficientInformation,
    Uncertain,
)
from tcg_domain.distribution import SUM_TOLERANCE, GradeDistribution
from tcg_domain.errors import (
    CurrencyMismatch,
    DomainError,
    InvalidCardReference,
    InvalidConfidence,
    InvalidGrade,
    InvalidGradeDistribution,
    InvalidMoney,
)
from tcg_domain.grade import MAX_GRADE, MIN_GRADE, Grade, GradeBound
from tcg_domain.money import Currency, Money

__all__ = [
    "ENGLISH",
    "INSUFFICIENT_INFORMATION",
    "JAPANESE",
    "MAX_GRADE",
    "MIN_GRADE",
    "POKEMON",
    "SUM_TOLERANCE",
    "CardReference",
    "Confidence",
    "Currency",
    "CurrencyMismatch",
    "DomainError",
    "Grade",
    "GradeBound",
    "GradeDistribution",
    "InsufficientInformation",
    "InvalidCardReference",
    "InvalidConfidence",
    "InvalidGrade",
    "InvalidGradeDistribution",
    "InvalidMoney",
    "Money",
    "Uncertain",
]
