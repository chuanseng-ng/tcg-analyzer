"""What the image-quality gate concluded — spec §19, issue #36.

Spec §19 names eleven conditions the system must detect and four statuses it may
conclude, and fixes what two of those statuses mean for the pipeline: `unusable`
stops the analysis, `poor` continues but the user must be told. `analysis.py`
already holds :class:`~tcg_domain.analysis.QualityStatus`, because that
vocabulary is a CHECK constraint on `images.quality_status`. What is here is the
gate's *output*: the eleven conditions, the three things that may be said about
one of them, and the report that carries them.

**Why this is in the domain rather than beside the heuristic.** The gate itself
is OpenCV in M2 and a model in M7, and it lives in `ml/image-quality` so that
the internet-facing API image never acquires the CV stack. But the API reads
`images.quality_details` back out of PostgreSQL and renders it into a response,
so it needs these words — and if they lived beside the implementation it would
have to import OpenCV to serve a GET. This module is stdlib-only, like every
other module here, and `test_domain_purity.py` enforces it.

**Undetermined is a first-class answer, not a gap.** Five of §19's eleven —
perspective distortion, card partly outside frame, multiple cards, sleeve
obstruction, insufficient card size — cannot be decided until the card has been
located in the photograph, which is #37's work. The acceptance criterion allows
exactly this: every condition is "detected or explicitly reported as
undetermined". Spec §2.7 says the same thing in general terms, and
:class:`~tcg_domain.confidence.InsufficientInformation` is the in-process form
of it. :data:`ConditionVerdict.UNDETERMINED` is its persisted form — an enum
member rather than the singleton, because this value is stored as JSON and
served over HTTP.

**What `acceptable` means.** §19 lists four statuses and defines none of them.
:meth:`QualityReport.status` fixes the reading: `good` is all eleven conditions
clear; `acceptable` is nothing wrong found *but something not checked*; `poor`
and `unusable` are the worst severity actually detected. A consequence worth
knowing before it surprises somebody: while the geometric five are always
undetermined, **no image can score `good`** — the gate has not looked at
everything. `good` becomes reachable the moment #37 supplies a card geometry,
with no change here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from tcg_domain.analysis import QualityStatus
from tcg_domain.errors import InvalidQualityReport

__all__ = [
    "DECIDABLE_WITHOUT_GEOMETRY",
    "NEEDS_CARD_GEOMETRY",
    "ConditionVerdict",
    "QualityCondition",
    "QualityFinding",
    "QualityReport",
    "worst_status",
]


class QualityCondition(StrEnum):
    """Spec §19's eleven conditions, in the order the specification lists them.

    A closed list for the reason :class:`~tcg_domain.analysis.AnalysisStatus` is
    one: a condition nobody wrote a detector for is a condition no image can be
    refused for. A twelfth is a feature, and it arrives with the heuristic that
    decides it and the copy that explains it.
    """

    BLUR = "blur"
    LOW_RESOLUTION = "low_resolution"
    GLARE = "glare"
    POOR_EXPOSURE = "poor_exposure"
    EXCESSIVE_DARKNESS = "excessive_darkness"
    EXCESSIVE_BRIGHTNESS = "excessive_brightness"
    SEVERE_PERSPECTIVE_DISTORTION = "severe_perspective_distortion"
    CARD_PARTLY_OUTSIDE_FRAME = "card_partly_outside_frame"
    MULTIPLE_CARDS = "multiple_cards"
    SLEEVE_OBSTRUCTION = "sleeve_obstruction"
    INSUFFICIENT_CARD_SIZE = "insufficient_card_size"


class ConditionVerdict(StrEnum):
    """What the gate was able to say about one condition.

    Three values rather than a boolean, because the third is the point. A gate
    that reported "no glare" when it had not looked for glare would be the
    confidently-wrong output spec §2.7 exists to forbid.
    """

    #: Looked for, not found.
    CLEAR = "clear"
    #: Looked for, found — the finding carries how badly.
    DETECTED = "detected"
    #: Not decidable from what the gate had. Carries a reason.
    UNDETERMINED = "undetermined"


#: The conditions a photograph alone answers: they are photometric, measurable
#: from the pixels without knowing where the card is.
DECIDABLE_WITHOUT_GEOMETRY: Final = frozenset(
    {
        QualityCondition.BLUR,
        QualityCondition.LOW_RESOLUTION,
        QualityCondition.GLARE,
        QualityCondition.POOR_EXPOSURE,
        QualityCondition.EXCESSIVE_DARKNESS,
        QualityCondition.EXCESSIVE_BRIGHTNESS,
    }
)

#: The conditions that need the card located first — #37's card boundary
#: detection, which its own scope says "feeds back into the quality gate". Until
#: it lands these are reported :data:`ConditionVerdict.UNDETERMINED`, never
#: guessed and never quietly omitted.
NEEDS_CARD_GEOMETRY: Final = frozenset(QualityCondition) - DECIDABLE_WITHOUT_GEOMETRY

#: How bad each status is. `good` is the absence of a finding, so a fold over an
#: empty set of concerns lands there; the two a detected finding may carry sit
#: above `acceptable`, which means "nothing found, something unchecked".
_SEVERITY_ORDER: Final[Mapping[QualityStatus, int]] = {
    QualityStatus.GOOD: 0,
    QualityStatus.ACCEPTABLE: 1,
    QualityStatus.POOR: 2,
    QualityStatus.UNUSABLE: 3,
}

#: What a `detected` finding may say about itself. Neither `good` nor
#: `acceptable` describes something that was found wrong.
DETECTABLE_SEVERITIES: Final = (QualityStatus.POOR, QualityStatus.UNUSABLE)

_NO_THRESHOLDS: Final[Mapping[str, float]] = MappingProxyType({})


def worst_status(statuses: Iterable[QualityStatus]) -> QualityStatus:
    """The most severe of `statuses`, or `good` when there are none.

    The fold lives here rather than beside the gate because three things have to
    agree on it: the gate folding eleven findings into one image's verdict, the
    job runner folding two images into one analysis's, and the tests. A `max`
    inlined at each of those is three places to get the ordering wrong.
    """
    return max(statuses, key=_SEVERITY_ORDER.__getitem__, default=QualityStatus.GOOD)


def _validated_measurement(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidQualityReport(
            f"measurement must be a real number or None, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise InvalidQualityReport(f"measurement must be finite, got {number!r}")
    return number


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """What the gate concluded about one of spec §19's eleven conditions.

    Args:
        condition: Which condition this is about.
        verdict: Whether it was found, ruled out, or could not be decided.
        severity: For a `detected` finding, what it makes the image — `poor` or
            `unusable`. Absent for the other two verdicts.
        measurement: The number the heuristic actually measured, for M7 to
            compare its model against. Internal: never shown to a user.
        reason: Why an `undetermined` verdict could not be reached. Required for
            that verdict, because "undetermined" with no explanation is a gap
            wearing an answer's clothes.

    Raises:
        InvalidQualityReport: If the verdict and the fields do not agree.
    """

    condition: QualityCondition
    verdict: ConditionVerdict
    severity: QualityStatus | None = None
    measurement: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "condition", QualityCondition(self.condition))
        set_field(self, "verdict", ConditionVerdict(self.verdict))
        set_field(self, "measurement", _validated_measurement(self.measurement))

        if self.verdict is ConditionVerdict.DETECTED:
            if self.severity not in DETECTABLE_SEVERITIES:
                raise InvalidQualityReport(
                    f"a detected {self.condition} must be poor or unusable, got {self.severity!r}"
                )
        elif self.severity is not None:
            raise InvalidQualityReport(
                f"only a detected condition carries a severity, but {self.condition} is "
                f"{self.verdict} and carries {self.severity!r}"
            )

        if self.verdict is ConditionVerdict.UNDETERMINED and not (self.reason or "").strip():
            raise InvalidQualityReport(f"an undetermined {self.condition} must say why")

    @property
    def contribution(self) -> QualityStatus:
        """What this finding alone would make the image.

        `good` for a condition ruled out. `acceptable` for one that could not be
        decided — an unchecked condition must never look like a clean bill of
        health, and must never fail an image either.
        """
        # Read from `severity` rather than from `verdict`, so no assertion is
        # needed to narrow it: __post_init__ has already made the two equivalent,
        # and `python -O` drops assertions.
        if self.severity is not None:
            return self.severity
        return (
            QualityStatus.ACCEPTABLE
            if self.verdict is ConditionVerdict.UNDETERMINED
            else QualityStatus.GOOD
        )

    def __str__(self) -> str:
        if self.verdict is ConditionVerdict.DETECTED:
            return f"{self.condition}: {self.severity}"
        return f"{self.condition}: {self.verdict}"


@dataclass(frozen=True, slots=True)
class QualityReport:
    """The gate's verdict on one photograph — spec §19.

    Args:
        findings: One finding for each of :class:`QualityCondition`'s eleven
            members, exactly once. The completeness rule is the acceptance
            criterion made structural: a condition nobody assessed cannot be
            silently dropped, it has to be reported undetermined.
        score: A ``[0, 1]`` verdict, 1 being best. Spec §19 fixes the four
            statuses and says nothing about a scale, so the gate defines it —
            see `tcg_ml_image_quality` for the one this project uses.
        version: The identifier of the gate that produced this. Recorded rather
            than assumed, so M7's model can be compared against the baseline
            that actually ran; never a pointer to "current".
        thresholds: The values the gate ran with, copied on construction. What
            makes the record reproducible without knowing which configuration
            was live.

    Raises:
        InvalidQualityReport: If a condition is missing or repeated, the score is
            out of range, or the version is blank.
    """

    findings: tuple[QualityFinding, ...]
    score: float
    version: str
    thresholds: Mapping[str, float] = _NO_THRESHOLDS

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "findings", tuple(self.findings))
        set_field(self, "score", _validated_score(self.score))
        set_field(self, "version", _validated_version(self.version))
        set_field(self, "thresholds", MappingProxyType(dict(self.thresholds)))

        seen = [finding.condition for finding in self.findings]
        if sorted(seen) != sorted(QualityCondition):
            missing = sorted(set(QualityCondition) - set(seen))
            repeated = sorted({c for c in seen if seen.count(c) > 1})
            raise InvalidQualityReport(
                "a report must carry every condition exactly once "
                f"(missing {missing}, repeated {repeated})"
            )

    @property
    def status(self) -> QualityStatus:
        """Spec §19's verdict for this image, derived rather than stored.

        Derived so that the status and the findings cannot disagree — a report
        saying `good` while carrying a detected blur is not representable.
        """
        return worst_status(finding.contribution for finding in self.findings)

    def of(self, condition: QualityCondition) -> QualityFinding:
        """The finding for `condition`. Always present, by construction."""
        return next(finding for finding in self.findings if finding.condition is condition)

    def as_record(self) -> dict[str, object]:
        """The form persisted to `images.quality_details`.

        Plain JSON-compatible types, so the caller writes it to a JSONB column
        without a serializer knowing anything about this package.
        """
        return {
            "version": self.version,
            "thresholds": dict(self.thresholds),
            "findings": [
                {
                    "condition": str(finding.condition),
                    "verdict": str(finding.verdict),
                    **({} if finding.severity is None else {"severity": str(finding.severity)}),
                    **({} if finding.measurement is None else {"measurement": finding.measurement}),
                    **({} if finding.reason is None else {"reason": finding.reason}),
                }
                for finding in self.findings
            ],
        }

    def __str__(self) -> str:
        return f"{self.status} ({self.score:.2f}, {self.version})"


def _validated_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidQualityReport(f"score must be a real number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidQualityReport(f"score must be finite, got {number!r}")
    if not 0.0 <= number <= 1.0:
        raise InvalidQualityReport(f"score must lie in [0, 1], got {number!r}")
    return number


def _validated_version(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidQualityReport("version must be a non-empty identifier")
    if "latest" in value.lower():
        # The same refusal `catalog_version.py` makes, for the same reason: a
        # record naming "latest" is worthless the moment the gate changes.
        raise InvalidQualityReport(f"version must name a fixed gate, not {value!r}")
    return value.strip()
