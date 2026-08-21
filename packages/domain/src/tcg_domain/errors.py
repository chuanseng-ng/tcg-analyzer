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

:class:`CatalogUnavailable` is the one exception to that reading, and it earns
its place here rather than in an adapter: it is what
:class:`tcg_domain.repository.CardRepository` promises to raise when the
catalog cannot be reached, so that swapping one implementation for another
changes no caller's error handling and no driver exception ever escapes the
port (the rule ``tcg_shared.storage.errors`` already applies to object storage).
"""

from __future__ import annotations

__all__ = [
    "CatalogUnavailable",
    "CurrencyMismatch",
    "DomainError",
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


class InvalidCatalogRecord(DomainError, ValueError):
    """A `Set`, `Card`, `CardExternalId` or `CardDatabaseVersion` field is
    empty or malformed.

    One error for the three spec §10 tables rather than three near-identical
    ones: they are a single family, always built together by the same importer
    and the same adapter, and a caller that needs to know which record failed
    reads the message rather than the class.

    :class:`tcg_domain.catalog_version.CardDatabaseVersion` joins that family on
    the same reasoning. It is not a §10 record — it is the provenance of the run
    that wrote them — but it is built by that same importer, in the same
    transaction, and a caller that would handle the two differently does not
    exist.
    """


class InvalidCardIdentification(DomainError, ValueError):
    """An identification names something other than a card, or an unvalidated
    confidence. Spec §20 forbids acting on an uncertain match, which only holds
    if the confidence is a :class:`tcg_domain.confidence.Confidence`."""


class InvalidCardSearch(DomainError, ValueError):
    """A catalog query or the page answering it is not representable.

    Covers both directions of :meth:`tcg_domain.repository.CardRepository.search`:
    a filter that is empty rather than absent, and a page whose window does not
    describe the result set it claims.
    """


class InvalidCardGeometry(DomainError, ValueError):
    """A detected card boundary is not representable — spec §18, #37.

    Covers a quadrilateral that does not have four finite corners, one that is
    concave, and one whose corners do not run clockwise from the top left.
    That last is why this is an error rather than a convention: perspective
    correction reads the corners positionally, so an inconsistent order does
    not fail — it silently rotates or mirrors the card.
    """


class InvalidQualityReport(DomainError, ValueError):
    """An image-quality finding or report is not representable — spec §19, #36.

    Covers a report that does not carry every one of §19's eleven conditions, a
    finding whose verdict and severity contradict each other, an undetermined
    verdict with no reason, and a score outside ``[0, 1]``. The completeness
    rule is why this is an error rather than something the gate merely tries to
    honour: a condition nobody assessed must be *reported* undetermined, not
    quietly left out of the answer.
    """


class CatalogUnavailable(DomainError, ConnectionError):
    """The card catalog could not be reached.

    Not invalid input — the request was well-formed and the answer is simply
    not obtainable right now. Also a `ConnectionError`, because that is what it
    is, and an adapter must translate its driver's failure into this rather
    than letting it escape.
    """
