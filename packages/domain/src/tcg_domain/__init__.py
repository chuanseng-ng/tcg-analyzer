"""Framework-free core domain types for TCG Grading Advisor.

This package has **zero framework, database or provider dependencies** and must
keep them: it is imported by the API, by the analysis service and by every ML
module, so the invariants below exist exactly once and cannot drift
(see ``docs/adr/0001-language-boundaries-in-the-monorepo.md``).

Everything re-exported here is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_domain.analysis import (
    TERMINAL_STATUSES,
    V1_SIDES,
    AnalysisStatus,
    ImageSide,
    QualityStatus,
    SessionStatus,
)
from tcg_domain.annotation import (
    LABELS_BY_KIND,
    NO_DEFECT_LABELS,
    REGIONS_BY_KIND,
    AnnotationKind,
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_domain.card import (
    ENGLISH,
    JAPANESE,
    POKEMON,
    CardReference,
    Game,
    Language,
)
from tcg_domain.card_geometry import CORNER_NAMES, CardGeometry, Corner
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
from tcg_domain.dataset import DatasetSplit
from tcg_domain.distribution import SUM_TOLERANCE, GradeDistribution
from tcg_domain.errors import (
    CatalogUnavailable,
    CurrencyMismatch,
    DomainError,
    InvalidCardGeometry,
    InvalidCardIdentification,
    InvalidCardReference,
    InvalidCardSearch,
    InvalidCatalogRecord,
    InvalidConfidence,
    InvalidGrade,
    InvalidGradeDistribution,
    InvalidMoney,
    InvalidQualityReport,
)
from tcg_domain.grade import MAX_GRADE, MIN_GRADE, Grade, GradeBound
from tcg_domain.identification import CardIdentification
from tcg_domain.image_quality import (
    DECIDABLE_WITHOUT_GEOMETRY,
    NEEDS_CARD_GEOMETRY,
    ConditionVerdict,
    QualityCondition,
    QualityFinding,
    QualityReport,
    worst_status,
)
from tcg_domain.money import Currency, Money
from tcg_domain.repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    CardPage,
    CardQuery,
    CardRepository,
)

__all__ = [
    "CORNER_NAMES",
    "DECIDABLE_WITHOUT_GEOMETRY",
    "DEFAULT_SEARCH_LIMIT",
    "ENGLISH",
    "INSUFFICIENT_INFORMATION",
    "JAPANESE",
    "LABELS_BY_KIND",
    "MAX_GRADE",
    "MAX_SEARCH_LIMIT",
    "MIN_GRADE",
    "NEEDS_CARD_GEOMETRY",
    "NO_DEFECT_LABELS",
    "POKEMON",
    "REGIONS_BY_KIND",
    "SUM_TOLERANCE",
    "TERMINAL_STATUSES",
    "V1_SIDES",
    "VERSION_PATTERN",
    "AnalysisStatus",
    "AnnotationKind",
    "Card",
    "CardDatabaseVersion",
    "CardDatabaseVersionRepository",
    "CardExternalId",
    "CardGeometry",
    "CardId",
    "CardIdentification",
    "CardPage",
    "CardQuery",
    "CardReference",
    "CardRepository",
    "CatalogUnavailable",
    "ConditionVerdict",
    "Confidence",
    "Corner",
    "CornerLabel",
    "CornerRegion",
    "Currency",
    "CurrencyMismatch",
    "DatasetSplit",
    "DefectSeverity",
    "DomainError",
    "EdgeLabel",
    "EdgeRegion",
    "Game",
    "Grade",
    "GradeBound",
    "GradeDistribution",
    "ImageSide",
    "InsufficientInformation",
    "InvalidCardGeometry",
    "InvalidCardIdentification",
    "InvalidCardReference",
    "InvalidCardSearch",
    "InvalidCatalogRecord",
    "InvalidConfidence",
    "InvalidGrade",
    "InvalidGradeDistribution",
    "InvalidMoney",
    "InvalidQualityReport",
    "Language",
    "Money",
    "QualityCondition",
    "QualityFinding",
    "QualityReport",
    "QualityStatus",
    "SessionStatus",
    "Set",
    "SetId",
    "SurfaceLabel",
    "Uncertain",
    "worst_status",
]
