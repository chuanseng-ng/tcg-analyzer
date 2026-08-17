"""A probability distribution over grades — the system's central invariant.

Spec §2.1 requires the ML system to produce a *distribution*, not a single
predicted grade, and requires that distribution be retained in full even when
the UI surfaces one expected grade. Spec §63 requires every distribution to
satisfy ``0 <= P(g) <= 1`` and ``Σ P(g) ≈ 1``, and requires the API to reject
model output that does not.

:class:`GradeDistribution` enforces that *in its constructor*. An invalid
distribution is impossible to construct, which is stronger than detecting one
downstream: no caller can forget the check, and no invalid distribution can
exist long enough to be persisted, serialised or shown to a user.

There is deliberately no lossy "expected grade only" representation. The
distribution is the value; :attr:`~GradeDistribution.most_likely_grade` is a
view of it.
"""

from __future__ import annotations

import math
from collections.abc import ItemsView, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from tcg_domain.errors import InvalidGrade, InvalidGradeDistribution
from tcg_domain.grade import Grade

__all__ = ["SUM_TOLERANCE", "GradeDistribution"]

#: How far ``Σ P(g)`` may drift from 1 and still be accepted (spec §63's "≈").
#:
#: Model output arrives as binary floats, so an exactly-1.0 sum cannot be
#: required: a softmax over four terms routinely lands a few ulps away. 1e-6 is
#: far wider than that accumulated error and far narrower than any real
#: modelling mistake — a distribution that is wrong is wrong by percentage
#: points, not by parts per million.
SUM_TOLERANCE: Final[float] = 1e-6


def _validated_probability(grade: Grade, probability: object) -> float:
    """Coerce one term's probability, rejecting anything spec §63 forbids."""
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise InvalidGradeDistribution(
            f"P({grade}) must be a real number, got {type(probability).__name__}"
        )
    value = float(probability)
    if not math.isfinite(value):
        raise InvalidGradeDistribution(f"P({grade}) must be finite, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise InvalidGradeDistribution(f"P({grade}) = {value!r} is outside [0, 1] (spec §63)")
    return value


@dataclass(frozen=True, slots=True)
class GradeDistribution:
    """A validated probability distribution over grades.

    Args:
        probabilities: The full distribution. Every probability must lie in
            ``[0, 1]`` and the probabilities must sum to 1 within
            :data:`SUM_TOLERANCE`.

    Raises:
        InvalidGradeDistribution: If the mapping is empty, or any probability
            is negative, above 1, or non-finite, or the sum is out of
            tolerance.
    """

    probabilities: Mapping[Grade, float]

    def __post_init__(self) -> None:
        source = self.probabilities
        if not isinstance(source, Mapping):
            raise InvalidGradeDistribution(
                f"expected a mapping of Grade to probability, got {type(source).__name__}"
            )
        if not source:
            raise InvalidGradeDistribution("a grade distribution must have at least one term")

        validated: dict[Grade, float] = {}
        for grade, probability in source.items():
            if not isinstance(grade, Grade):
                raise InvalidGradeDistribution(
                    f"distribution keys must be Grade, got {type(grade).__name__}"
                )
            validated[grade] = _validated_probability(grade, probability)

        total = math.fsum(validated.values())
        if abs(total - 1.0) > SUM_TOLERANCE:
            raise InvalidGradeDistribution(
                f"probabilities sum to {total!r}, which is further than {SUM_TOLERANCE} "
                "from 1 (spec §63)"
            )

        # Sorted and copied: sorted so serialisation is deterministic, copied so
        # a caller cannot mutate a distribution after it has been validated.
        ordered = {grade: validated[grade] for grade in sorted(validated)}
        object.__setattr__(self, "probabilities", MappingProxyType(ordered))

    # ----------------------------------------------------------------
    # Boundaries
    # ----------------------------------------------------------------

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, float]) -> GradeDistribution:
        """Build a distribution from a model's or an API payload's string keys.

        Args:
            mapping: Grade keys as spec §24 spells them —
                ``{"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}``.

        Raises:
            InvalidGradeDistribution: If a key is not a grade key, if two keys
                denote the same grade (``"9"`` and ``"9.0"``), or if the
                probabilities violate spec §63.
        """
        if not isinstance(mapping, Mapping):
            raise InvalidGradeDistribution(
                f"expected a mapping of grade key to probability, got {type(mapping).__name__}"
            )

        parsed: dict[Grade, float] = {}
        for key, probability in mapping.items():
            try:
                grade = Grade.parse(key)
            except InvalidGrade as exc:
                raise InvalidGradeDistribution(str(exc)) from exc
            if grade in parsed:
                raise InvalidGradeDistribution(f"grade {grade} appears more than once")
            parsed[grade] = probability

        return cls(parsed)

    def as_mapping(self) -> dict[str, float]:
        """Serialise back to string-keyed form for the API boundary.

        Keys are canonical and sorted, so two equal distributions serialise
        identically.
        """
        return {str(grade): probability for grade, probability in self.probabilities.items()}

    # ----------------------------------------------------------------
    # Reading
    # ----------------------------------------------------------------

    @property
    def most_likely_grade(self) -> Grade:
        """The single most probable grade — a view, never a replacement.

        Ties break toward the higher grade, so the result is deterministic.
        The full distribution remains available and must accompany this value
        wherever a prediction is recorded (spec §2.1).
        """

        def rank(grade: Grade) -> tuple[float, tuple[Decimal, int]]:
            return (self.probabilities[grade], grade.sort_key)

        return max(self.probabilities, key=rank)

    def probability_of(self, grade: Grade) -> float:
        """P(`grade`), or ``0.0`` when the distribution has no such term."""
        return self.probabilities.get(grade, 0.0)

    def items(self) -> ItemsView[Grade, float]:
        """The terms, in ascending grade order."""
        return self.probabilities.items()

    def __iter__(self) -> Iterator[Grade]:
        return iter(self.probabilities)

    def __len__(self) -> int:
        return len(self.probabilities)

    def __contains__(self, grade: object) -> bool:
        return grade in self.probabilities

    def __str__(self) -> str:
        terms = ", ".join(f"{grade}: {probability:g}" for grade, probability in self.items())
        return f"{{{terms}}}"
