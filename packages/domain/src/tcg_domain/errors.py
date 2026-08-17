"""The domain's exception hierarchy.

Every error raised by this package derives from :class:`DomainError`, so a
caller can catch the whole domain with one clause. Each concrete error also
derives from the closest builtin (`ValueError`), because rejecting bad input is
exactly what a `ValueError` means and callers should not have to know the
domain's private hierarchy to handle it.

These exceptions signal *invalid input* — a caller handed the domain something
it cannot represent. They are not the mechanism for expressing uncertainty:
"we do not know" is a legitimate result, not a failure, and is carried by
:class:`tcg_domain.confidence.InsufficientInformation` instead (spec §2.7).
"""

from __future__ import annotations

__all__ = [
    "CurrencyMismatch",
    "DomainError",
    "InvalidCardReference",
    "InvalidConfidence",
    "InvalidGrade",
    "InvalidGradeDistribution",
    "InvalidMoney",
]


class DomainError(Exception):
    """Base class for every error raised by the domain package."""


class InvalidGrade(DomainError, ValueError):
    """A grade key or value is outside the representable grading scale."""


class InvalidGradeDistribution(DomainError, ValueError):
    """A grade distribution violates the probability-validity rule of spec §63.

    Raised when a mapping is empty, contains a probability outside ``[0, 1]``,
    contains a non-finite probability, names the same grade twice, or does not
    sum to 1 within :data:`tcg_domain.distribution.SUM_TOLERANCE`.
    """


class InvalidMoney(DomainError, ValueError, ArithmeticError):
    """A monetary amount is not representable — most often a binary float.

    Also an `ArithmeticError`, because `Money.__mul__` raises it rather than
    returning `NotImplemented` for a float factor: returning `NotImplemented`
    would hand the operation to `float.__rmul__` and produce an unhelpful
    `TypeError` in place of the message naming `Decimal` as the fix. Since the
    exception escapes an arithmetic operator, it should be catchable as one.
    """


class CurrencyMismatch(DomainError, ValueError):
    """An arithmetic or comparison operation mixed two currencies."""


class InvalidConfidence(DomainError, ValueError):
    """A confidence value falls outside ``[0, 1]`` or is not finite."""


class InvalidCardReference(DomainError, ValueError):
    """A card reference field is empty or malformed."""
