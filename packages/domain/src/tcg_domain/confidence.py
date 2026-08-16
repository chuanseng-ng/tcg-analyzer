"""Confidence, and the right to say "I do not know".

Spec §2.7 makes uncertainty a product feature: the system must be able to
report "insufficient image quality to confidently assess surface condition",
and must never fabricate certainty. That has two consequences here.

* :class:`Confidence` is a validated number in ``[0, 1]`` — a bare `float`
  invites a "confidence" of 87 that means 87%, or -1 that means "unknown".
* :class:`InsufficientInformation` is a **result**, not an error. It never
  derives from `Exception` and must never be raised: a caller that receives it
  has a valid answer ("we cannot tell"), not a failure to handle. The alias
  :data:`Uncertain` spells that in a signature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import total_ordering

from tcg_domain.errors import InvalidConfidence

__all__ = [
    "INSUFFICIENT_INFORMATION",
    "Confidence",
    "InsufficientInformation",
    "Uncertain",
]


def _validated_unit_interval(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidConfidence(f"{label} must be a real number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidConfidence(f"{label} must be finite, got {number!r}")
    if not 0.0 <= number <= 1.0:
        raise InvalidConfidence(f"{label} must lie in [0, 1], got {number!r}")
    return number


@total_ordering
@dataclass(frozen=True, slots=True)
class Confidence:
    """How sure the system is, on a ``[0, 1]`` scale.

    Args:
        value: A finite number in ``[0, 1]``.

    Raises:
        InvalidConfidence: If the value is out of range or not finite.
    """

    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validated_unit_interval(self.value, label="confidence"))

    @classmethod
    def of(cls, value: float) -> Confidence:
        """Build a confidence — ``Confidence.of(0.82)``."""
        return cls(value)

    def is_below(self, threshold: float | Confidence) -> bool:
        """Whether this confidence falls strictly below `threshold`.

        Used wherever the pipeline must not act on a weak signal — a
        low-confidence card identification is never silently accepted.

        Raises:
            InvalidConfidence: If `threshold` is not in ``[0, 1]``.
        """
        limit = (
            threshold.value
            if isinstance(threshold, Confidence)
            else _validated_unit_interval(threshold, label="threshold")
        )
        return self.value < limit

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.value < other.value

    def __str__(self) -> str:
        return format(self.value, ".0%")


@dataclass(frozen=True, slots=True)
class InsufficientInformation:
    """A legitimate result meaning "the input does not support an answer".

    This is not an error and must never be raised. Surface analysis and the
    final recommendation may both return it (spec §2.7); a mediocre answer with
    honest uncertainty beats a confident wrong one.

    Args:
        reason: A human-readable explanation, when one is available.
    """

    reason: str | None = None

    def __bool__(self) -> bool:
        """Always falsy, so ``if result:`` cannot mistake it for an answer."""
        return False

    def __str__(self) -> str:
        return (
            "insufficient_information"
            if self.reason is None
            else f"insufficient_information: {self.reason}"
        )


#: The reasonless case, for callers that have nothing to add.
INSUFFICIENT_INFORMATION = InsufficientInformation()

#: A `T`, or an honest admission that no `T` could be determined.
type Uncertain[T] = T | InsufficientInformation
