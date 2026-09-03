"""The `GradingCompanyAdapter` port and the PSA / TAG / BGS grade scales.

Spec §22's abstraction, spec §23's versioned rules reference, and the three
companies' grade scales as data. It lands in M4 rather than M8 because spec
§35's ``market_observations`` keys a graded price by ``(grading_company,
grade)``, and that key cannot be written until something says which grades each
company issues.

The package depends on `tcg-domain` and nothing else. It reaches no network, no
database and no vendor SDK: an adapter here is published reference data, plus
whatever grading model it was handed at construction — never one it imports.

Everything re-exported here is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_grading_companies.companies import (
    ADAPTERS,
    BGS_RULES,
    BGS_SCALE,
    DESIGNATIONS,
    PSA_RULES,
    PSA_SCALE,
    TAG_RULES,
    TAG_SCALE,
    BGSAdapter,
    Designation,
    PSAAdapter,
    TAGAdapter,
)
from tcg_grading_companies.errors import (
    GradePredictionFailed,
    GradePredictionUnavailable,
    GradingCompanyError,
    UnsupportedGrade,
)
from tcg_grading_companies.port import (
    GradePrediction,
    GradePredictor,
    GradingCompany,
    GradingCompanyAdapter,
)
from tcg_grading_companies.reference import EMPTY_RULES, GradingRules, ServiceOption
from tcg_grading_companies.scale import GradeScale

__all__ = [
    "ADAPTERS",
    "BGS_RULES",
    "BGS_SCALE",
    "DESIGNATIONS",
    "EMPTY_RULES",
    "PSA_RULES",
    "PSA_SCALE",
    "TAG_RULES",
    "TAG_SCALE",
    "BGSAdapter",
    "Designation",
    "GradePrediction",
    "GradePredictionFailed",
    "GradePredictionUnavailable",
    "GradePredictor",
    "GradeScale",
    "GradingCompany",
    "GradingCompanyAdapter",
    "GradingCompanyError",
    "GradingRules",
    "PSAAdapter",
    "ServiceOption",
    "TAGAdapter",
    "UnsupportedGrade",
]
