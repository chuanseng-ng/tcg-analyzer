"""The grading-companies package's exception hierarchy.

Every error raised here derives from :class:`GradingCompanyError`, so a caller
can catch the whole package with one clause, and each concrete error also
derives from the closest builtin — the convention
:mod:`tcg_domain.errors` already sets, so that handling one of these never
requires knowing this package's private hierarchy.

Nothing here expresses uncertainty. "This model cannot tell" is a *result*,
carried by :data:`tcg_domain.confidence.INSUFFICIENT_INFORMATION`; these two
mean the caller asked for something the package cannot answer at all.
"""

from __future__ import annotations

__all__ = [
    "GradePredictionUnavailable",
    "GradingCompanyError",
    "UnsupportedGrade",
]


class GradingCompanyError(Exception):
    """Base class for every error raised by this package."""


class UnsupportedGrade(GradingCompanyError, ValueError):
    """A grade is not on the scale of the company it was offered to.

    The three companies do not share a scale — PSA and TAG issue no 9.5 and BGS
    does — so a distribution is only meaningful against the company that
    produced it. Raised by :meth:`tcg_grading_companies.scale.GradeScale.validate`.
    """


class GradePredictionUnavailable(GradingCompanyError, NotImplementedError):
    """No model exists yet to predict a grade for this company.

    Spec §24's per-company models arrive in M8. Until then every adapter's
    ``predict_grade`` raises this rather than returning a fabricated
    distribution — CLAUDE.md's "never fabricate certainty", made structural.

    Also a `NotImplementedError`, because that is exactly what it is: the
    contract is declared and the implementation is not here yet.
    """
