"""A grading company's grade scale — which grades it can actually issue.

Spec §24 requires BGS half grades and shows a distribution keyed by strings
such as ``{"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}``.
:class:`~tcg_domain.grade.Grade` already models both of those forms. What is
missing, and what this module supplies, is *which of those keys are legal for
which company* — the thing spec §35's ``(grading_company, grade)`` observation
key and M8's three models both need before either can be written.

The scales themselves are in :mod:`tcg_grading_companies.companies`. This
module is only the type and the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tcg_domain.errors import InvalidGrade
from tcg_domain.grade import Grade

from tcg_grading_companies.errors import UnsupportedGrade

if TYPE_CHECKING:
    from tcg_domain.distribution import GradeDistribution

__all__ = ["GradeScale"]


@dataclass(frozen=True, slots=True)
class GradeScale:
    """Every grade one company can issue, as versioned reference data.

    Args:
        company: The company's lowercase slug — see
            :class:`tcg_grading_companies.port.GradingCompany`.
        version: The version of the published reference this scale was read
            from. It is the *same* string as the company's
            :attr:`~tcg_grading_companies.reference.GradingRules.version`: a
            scale and the rules that define it are one published artifact, and
            two version namespaces would be two things to keep in step.
        grades: The exact grades on the scale. Bucket grades such as
            ``7_or_lower`` are rejected — a scale names points, and which
            buckets are legal follows from those points via :meth:`supports`.

    Raises:
        InvalidGrade: If `grades` is empty or contains a bucket grade.

    Note:
        `company` and `version` are not validated. Every scale in this package
        is a module-level literal a few lines from its own docstring, not input
        arriving from anywhere, and validating one would be ceremony rather
        than a trust boundary. The grade checks below are different: they
        encode the invariant :meth:`supports` depends on.
    """

    company: str
    version: str
    grades: frozenset[Grade]

    def __post_init__(self) -> None:
        if not isinstance(self.grades, frozenset):
            raise InvalidGrade(
                f"grades must be a frozenset of Grade, got {type(self.grades).__name__}"
            )
        if not self.grades:
            raise InvalidGrade(f"the {self.company} grade scale must name at least one grade")
        for grade in self.grades:
            if not isinstance(grade, Grade):
                raise InvalidGrade(f"grades must contain Grade, got {type(grade).__name__}")
            if grade.is_bucket:
                raise InvalidGrade(
                    f"a grade scale names points, not collapsed tails: {grade} is a bucket. "
                    "Which buckets are legal follows from the points — see GradeScale.supports()."
                )

    @property
    def ordered(self) -> tuple[Grade, ...]:
        """The scale ascending.

        `Grade` is already totally ordered, so nothing here reimplements the
        rule that ``8.5`` sits between ``8`` and ``9``.
        """
        return tuple(sorted(self.grades))

    def supports(self, grade: Grade) -> bool:
        """Whether `grade` is a legal key in a distribution from this company.

        Not plain membership. Spec §24's own example collapses a tail into
        ``7_or_lower``, so a bucket is legal exactly when **its value names a
        grade on the scale**: ``9.5_or_higher`` is legal for BGS and illegal
        for PSA, which falls out of the scales rather than needing a second
        rule.

        There is no `isinstance` guard: every string key already becomes a
        `Grade` at the one boundary where one arrives, in
        `GradeDistribution.from_mapping`, and mypy covers the rest.
        """
        return Grade(grade.value) in self.grades

    def validate(self, distribution: GradeDistribution) -> None:
        """Refuse a distribution carrying a grade this company cannot issue.

        Raises:
            UnsupportedGrade: Naming the offending key — the lowest, since a
                distribution iterates in ascending grade order — and the
                company that cannot issue it.
        """
        for grade in distribution:
            if not self.supports(grade):
                raise UnsupportedGrade(
                    f"{self.company} does not issue grade {grade}; its scale is "
                    f"{', '.join(str(item) for item in self.ordered)}"
                )

    def __str__(self) -> str:
        return f"{self.company} {self.version}: {', '.join(str(g) for g in self.ordered)}"
