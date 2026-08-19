"""Framework-free core domain types for TCG Grading Advisor.

This package has **zero framework, database or provider dependencies** and must
keep them: it is imported by the API, by the analysis service and by every ML
module, so the invariants below exist exactly once and cannot drift
(see ``docs/adr/0001-language-boundaries-in-the-monorepo.md``).

Everything re-exported here is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_domain.card import (
    ENGLISH,
    JAPANESE,
    POKEMON,
    CardReference,
    Game,
    Language,
)
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.catalog_version import (
    VERSION_PATTERN,
    CardDatabaseVersion,
    CardDatabaseVersionRepository,
)
from tcg_domain.confidence import (
    INSUFFICIENT_INFORMATION,
    Confidence,
    InsufficientInformation,
    Uncertain,
)
from tcg_domain.distribution import SUM_TOLERANCE, GradeDistribution
from tcg_domain.errors import (
    CatalogUnavailable,
    CurrencyMismatch,
    DomainError,
    InvalidCardIdentification,
    InvalidCardReference,
    InvalidCardSearch,
    InvalidCatalogRecord,
    InvalidConfidence,
    InvalidGrade,
    InvalidGradeDistribution,
    InvalidMoney,
)
from tcg_domain.grade import MAX_GRADE, MIN_GRADE, Grade, GradeBound
from tcg_domain.identification import CardIdentification
from tcg_domain.money import Currency, Money
from tcg_domain.repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    CardPage,
    CardQuery,
    CardRepository,
)

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "ENGLISH",
    "INSUFFICIENT_INFORMATION",
    "JAPANESE",
    "MAX_GRADE",
    "MAX_SEARCH_LIMIT",
    "MIN_GRADE",
    "POKEMON",
    "SUM_TOLERANCE",
    "VERSION_PATTERN",
    "Card",
    "CardDatabaseVersion",
    "CardDatabaseVersionRepository",
    "CardExternalId",
    "CardId",
    "CardIdentification",
    "CardPage",
    "CardQuery",
    "CardReference",
    "CardRepository",
    "CatalogUnavailable",
    "Confidence",
    "Currency",
    "CurrencyMismatch",
    "DomainError",
    "Game",
    "Grade",
    "GradeBound",
    "GradeDistribution",
    "InsufficientInformation",
    "InvalidCardIdentification",
    "InvalidCardReference",
    "InvalidCardSearch",
    "InvalidCatalogRecord",
    "InvalidConfidence",
    "InvalidGrade",
    "InvalidGradeDistribution",
    "InvalidMoney",
    "Language",
    "Money",
    "Set",
    "SetId",
    "Uncertain",
]
