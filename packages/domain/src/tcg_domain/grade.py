"""A single grade on a grading company's scale.

Spec §24 shows grading-model output keyed by strings such as
``{"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}``, and requires BGS
half grades. :class:`Grade` models exactly those forms:

* an exact grade — ``"10"``, ``"9"``, ``"9.5"``;
* a bucketed tail or head — ``"7_or_lower"``, ``"9_or_higher"`` — which a model
  emits when the distribution's extremes are collapsed into one term.

Values are :class:`~decimal.Decimal` multiples of ``0.5`` within ``[0, 10]``.
Binary floats are rejected outright: ``0.1 + 0.2 != 0.3`` has no place in a
value that is compared, hashed and used as a mapping key.

Grades are totally ordered so that a UI can sort a distribution without
reimplementing the rule: numeric grades order by value, ``X_or_lower`` orders
immediately below ``X`` and ``X_or_higher`` immediately above it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import total_ordering
from typing import Final, assert_never

from tcg_domain.errors import InvalidGrade

__all__ = ["MAX_GRADE", "MIN_GRADE", "Grade", "GradeBound"]

MIN_GRADE: Final = Decimal("0")
MAX_GRADE: Final = Decimal("10")
_STEP: Final = Decimal("0.5")

#: Bucket keys, per the design: ``^\d+(\.\d+)?_or_(lower|higher)$``.
_BUCKET_PATTERN: Final = re.compile(r"^(?P<value>\d+(?:\.\d+)?)_or_(?P<bound>lower|higher)$")
_NUMERIC_PATTERN: Final = re.compile(r"^\d+(?:\.\d+)?$")


class GradeBound(StrEnum):
    """Whether a grade names one point on the scale or a collapsed tail."""

    EXACT = "exact"
    OR_LOWER = "or_lower"
    OR_HIGHER = "or_higher"

    @property
    def sort_offset(self) -> int:
        """Rank a bound against an exact grade of the same value."""
        match self:
            case GradeBound.OR_LOWER:
                return -1
            case GradeBound.EXACT:
                return 0
            case GradeBound.OR_HIGHER:
                return 1
            case _:
                # Unreachable today. A fourth member added without a case would
                # otherwise return `None`, which `sort_key` carries into a
                # comparison that fails far from the omission.
                assert_never(self)


def _canonical(value: object) -> Decimal:
    """Normalise a grade value so equal grades render identically.

    ``Decimal("9.0")`` and ``Decimal("9")`` are numerically equal but render
    differently, which would give one grade two spellings on the API boundary.
    Whole values canonicalise to ``9``; half values to ``9.5``.

    Typed `object` rather than `Decimal` for the same reason as `_quantised`:
    grade values arrive from model output and API payloads, so the float
    rejection below is a runtime guard, not a redundant check on a type the
    checker has already proven.
    """
    if isinstance(value, float):
        raise InvalidGrade(
            f"grade values must be Decimal, not float: {value!r}. "
            "Binary floats cannot represent the grading scale exactly."
        )
    if not isinstance(value, Decimal):
        raise InvalidGrade(
            f"grade values must be Decimal, got {type(value).__name__}; "
            "use Grade.parse() to build a Grade from a model's string key"
        )

    if not value.is_finite():
        raise InvalidGrade(f"grade values must be finite: {value!r}")
    if not MIN_GRADE <= value <= MAX_GRADE:
        raise InvalidGrade(f"grade {value} is outside [{MIN_GRADE}, {MAX_GRADE}]")
    if value % _STEP != 0:
        raise InvalidGrade(f"grade {value} is not a multiple of {_STEP}")

    if value == value.to_integral_value():
        return value.quantize(Decimal("1"))
    return value.quantize(Decimal("0.1"))


@total_ordering
@dataclass(frozen=True, slots=True)
class Grade:
    """One term of a grade distribution.

    Args:
        value: A `Decimal` multiple of ``0.5`` in ``[0, 10]``. Anything else —
            a `float` above all — is rejected; use :meth:`parse` for the
            string keys a model emits.
        bound: Whether the grade is exact or a collapsed tail/head.

    Raises:
        InvalidGrade: If the value is not representable on the scale.
    """

    value: Decimal
    bound: GradeBound = GradeBound.EXACT

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _canonical(self.value))
        if not isinstance(self.bound, GradeBound):
            raise InvalidGrade(f"unsupported grade bound: {self.bound!r}")

    @classmethod
    def parse(cls, key: str) -> Grade:
        """Parse a grade key as produced by a grading model (spec §24).

        ``Grade.parse`` and :meth:`__str__` are inverses for canonical keys;
        a non-canonical spelling such as ``"9.0"`` parses and then renders as
        ``"9"``.

        Raises:
            InvalidGrade: If `key` is not a recognised grade key.
        """
        if not isinstance(key, str):
            raise InvalidGrade(f"grade keys must be strings, got {type(key).__name__}")

        candidate = key.strip()
        if candidate != key or not candidate:
            raise InvalidGrade(f"unrecognised grade key: {key!r}")

        if _NUMERIC_PATTERN.match(candidate):
            return cls(Decimal(candidate))

        match = _BUCKET_PATTERN.match(candidate)
        if match is None:
            raise InvalidGrade(f"unrecognised grade key: {key!r}")
        bound = GradeBound.OR_LOWER if match["bound"] == "lower" else GradeBound.OR_HIGHER
        return cls(Decimal(match["value"]), bound)

    @property
    def is_bucket(self) -> bool:
        """True when this grade collapses a tail rather than naming one point."""
        return self.bound is not GradeBound.EXACT

    @property
    def sort_key(self) -> tuple[Decimal, int]:
        """The total order: by value, then bucket position around that value."""
        return (self.value, self.bound.sort_offset)

    def __str__(self) -> str:
        rendered = format(self.value, "f")
        match self.bound:
            case GradeBound.EXACT:
                return rendered
            case GradeBound.OR_LOWER:
                return f"{rendered}_or_lower"
            case GradeBound.OR_HIGHER:
                return f"{rendered}_or_higher"
            case _:
                # As above: silently returning `None` from `__str__` becomes a
                # `TypeError` in whatever formats the grade, not here.
                assert_never(self.bound)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Grade):
            return NotImplemented
        return self.sort_key < other.sort_key
