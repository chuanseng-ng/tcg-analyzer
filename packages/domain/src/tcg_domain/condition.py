"""The neutral condition representation — spec §13 and §17, issue #180.

This is the hinge of spec §2.2: one condition representation, entirely
independent of any grading company, that every company model consumes. The
master architectural rule — grading separate from condition, no universal
``condition_score → grade`` mapping — depends on this module carrying **no
company vocabulary, no grade and no score**, and it never will.
:meth:`tcg_grading_companies.port.GradingCompanyAdapter.predict_grade` stays
typed ``object`` through all of M7; M8 narrows it to
:class:`ConditionAssessment` in its own change.

The vocabularies are :mod:`tcg_domain.annotation`'s, reused and never copied —
that module's docstring names this one as the second consumer it was placed in
the domain for. A label here is an **enum member, never a bare string**:
``"whitening"`` is in two vocabularies and ``"dent"`` in three, so a string
cannot be checked against the right list, where the member carries its list
with it.

Two rules come from #175 and epic #8's decomposition, and both are structural:

* **Spatial data names the frame its coordinates are fractions of** —
  :class:`Representation`, the same ``normalized | original`` pair as
  `image_annotations.representation`. A coordinate is **never projected
  between the two frames**; the frames relate by a projective warp, and a
  helpful conversion would corrupt silently. No such helper exists here and
  none may be added. Only a *surface* defect may declare the original
  photograph (the database's `only_a_surface_marks_the_original`, mirrored);
  a corner or edge finding is always against the normalized artifact, which
  is why :class:`RegionFinding` carries no frame field at all.
* **Uncertainty is a member, not a wrapper to invent.** Every axis admits
  :class:`~tcg_domain.confidence.InsufficientInformation` through the existing
  :data:`~tcg_domain.confidence.Uncertain` idiom. ``eye_appeal`` is typed as
  the refusal itself: §13 names it, no spec section defines it, and nothing in
  V1 can measure it — a later version that can widens the type, which keeps
  every existing value legal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.annotation import (
    NO_DEFECT_LABELS,
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain
from tcg_domain.errors import InvalidConditionAssessment

__all__ = [
    "BoundingBox",
    "Centering",
    "ConditionAssessment",
    "Defect",
    "Polygon",
    "RegionFinding",
    "Representation",
    "SurfaceAssessment",
    "card_frame_of",
]


class Representation(StrEnum):
    """The frame a spatial claim's fractions are fractions of — #175.

    Byte-identical to `image_annotations.representation`'s pair. The service's
    constants live in ``tcg_api.datasets.annotation`` and are deliberately not
    importable from here (the domain must not acquire the service); the
    duplication is on purpose, `tcg_api.datasets.tables`' precedent, and the
    transcription test is what keeps the two spellings from drifting.
    """

    #: The normalized artifact — what annotators see and what
    #: every corner, edge and coarse surface claim is measured against.
    NORMALIZED = "normalized"
    #: The original photograph — ADR 0010's one route back to a fine-class
    #: surface signal, and a surface claim's frame only.
    ORIGINAL = "original"


def _validated_fraction(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidConditionAssessment(
            f"{label} must be a real number, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise InvalidConditionAssessment(f"{label} must be finite, got {number!r}")
    if not 0.0 <= number <= 1.0:
        raise InvalidConditionAssessment(f"{label} must lie in [0, 1], got {number!r}")
    return number


def _validated_confidence(value: object, *, owner: str) -> Confidence:
    if not isinstance(value, Confidence):
        raise InvalidConditionAssessment(
            f"{owner} confidence must be a Confidence, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Where a defect sits, as fractions of its declared frame.

    The same rule as `ck_image_annotations_bounding_box_lies_inside_the_artifact`:
    the box lies inside the unit square and has area. Which frame the fractions
    belong to is the owning :class:`Defect`'s ``representation`` — the box
    itself is frame-agnostic, exactly as the database constraint is.

    Raises:
        InvalidConditionAssessment: If a coordinate is outside the unit square,
            a dimension is not strictly positive, or the box overruns an edge.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        set_field(self, "x", _validated_fraction(self.x, label="bounding box x"))
        set_field(self, "y", _validated_fraction(self.y, label="bounding box y"))
        set_field(self, "width", _validated_fraction(self.width, label="bounding box width"))
        set_field(self, "height", _validated_fraction(self.height, label="bounding box height"))
        if self.width == 0.0 or self.height == 0.0:
            raise InvalidConditionAssessment("a bounding box must have area")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise InvalidConditionAssessment(
                f"a bounding box must lie inside the unit square, got x + width = "
                f"{self.x + self.width!r}, y + height = {self.y + self.height!r}"
            )


#: Spec §17's polygon: at least three points, each a fraction pair in the same
#: frame as the owning defect's bounding box.
type Polygon = tuple[tuple[float, float], ...]

_NO_METADATA: Final[Mapping[str, object]] = MappingProxyType({})


def _coerced[MemberT: StrEnum](enum: type[MemberT], value: object) -> MemberT:
    """Coerce a stored-string field to its member, refusing as the domain error.

    A bare ``ValueError`` escaping a constructor would break `errors.py`'s
    catch-the-whole-domain-with-one-clause invariant.
    """
    try:
        return enum(value)  # type: ignore[arg-type]
    except ValueError as error:
        raise InvalidConditionAssessment(str(error)) from error


def _validated_polygon(value: object) -> Polygon | None:
    if value is None:
        return None
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raise InvalidConditionAssessment(
            f"a polygon must be a sequence of points, got {type(value).__name__}"
        )
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, Iterable) or isinstance(point, (str, bytes)):
            raise InvalidConditionAssessment(
                f"a polygon point must be an (x, y) pair, got {point!r}"
            )
        coordinates = tuple(point)
        if len(coordinates) != 2:
            raise InvalidConditionAssessment(
                f"a polygon point must be an (x, y) pair, got {point!r}"
            )
        points.append(
            (
                _validated_fraction(coordinates[0], label="polygon x"),
                _validated_fraction(coordinates[1], label="polygon y"),
            )
        )
    if len(points) < 3:
        raise InvalidConditionAssessment(
            f"a polygon needs at least three points, got {len(points)}"
        )
    return tuple(points)


@dataclass(frozen=True, slots=True)
class Defect:
    """One defect found on one side of the card — spec §17, verbatim fields.

    Spatial data is captured from day one even though visualization is post-V1,
    and ``representation`` is what makes it data: fractions that do not name
    their frame are numbers (#175). ``type`` admits :class:`SurfaceLabel` and
    :class:`EdgeLabel` and deliberately not :class:`CornerLabel` — corners
    travel as :class:`RegionFinding`, and ``manufacturing_defects`` derives
    from surface and edge findings only (epic #8, decision 3).

    Args:
        type: What the defect is — a vocabulary member, never a bare string,
            and never ``clean`` (a defect asserting no defect).
        confidence: How sure the finder is.
        severity: #158's ordinal. ``None`` exactly when the type is ``unknown``
            — damage seen but not nameable cannot be rated either.
        side: Which face of the card the defect is on.
        representation: The frame ``bounding_box`` and ``polygon`` are
            fractions of. ``original`` is a surface defect's privilege only.
        bounding_box: Where the defect sits, when the finder can say.
        polygon: A finer outline, in the same frame.
        metadata: §17's open bag — copied and frozen on construction.

    Raises:
        InvalidConditionAssessment: If any field breaks the rules above.
    """

    type: SurfaceLabel | EdgeLabel
    confidence: Confidence
    severity: DefectSeverity | None
    side: ImageSide
    representation: Representation
    bounding_box: BoundingBox | None = None
    polygon: Polygon | None = None
    metadata: Mapping[str, object] = _NO_METADATA

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        if not isinstance(self.type, (SurfaceLabel, EdgeLabel)):
            raise InvalidConditionAssessment(
                f"a defect type must be a SurfaceLabel or EdgeLabel member, got {self.type!r} "
                "— a bare string is ambiguous, since several labels are in more than one "
                "vocabulary"
            )
        if self.type.value == "clean":
            raise InvalidConditionAssessment(
                "clean is not a defect: a clean corner or edge is a RegionFinding, "
                "a clean surface is the absence of findings"
            )
        _validated_confidence(self.confidence, owner="a defect's")
        if self.type.value == "unknown":
            if self.severity is not None:
                raise InvalidConditionAssessment(
                    f"an unknown defect cannot be rated, but carries {self.severity!r}"
                )
        elif not isinstance(self.severity, DefectSeverity):
            raise InvalidConditionAssessment(
                f"a {self.type} defect must carry a severity (spec §17), got {self.severity!r}"
            )
        set_field(self, "side", _coerced(ImageSide, self.side))
        set_field(self, "representation", _coerced(Representation, self.representation))
        if self.representation is Representation.ORIGINAL and not isinstance(
            self.type, SurfaceLabel
        ):
            raise InvalidConditionAssessment(
                f"only a surface defect marks the original photograph (#175), "
                f"but {self.type} declares it"
            )
        if self.bounding_box is not None and not isinstance(self.bounding_box, BoundingBox):
            raise InvalidConditionAssessment(
                f"a bounding box must be a BoundingBox, got {type(self.bounding_box).__name__}"
            )
        set_field(self, "polygon", _validated_polygon(self.polygon))
        set_field(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RegionFinding:
    """What an analyzer concluded about one corner or one edge — spec §14's
    "each result": classification, confidence, severity, spatial information
    where available.

    Which corner or edge, and on which side, is the position in the owning
    assessment's mappings — repeating either here would let the two disagree,
    :mod:`tcg_domain.annotation`'s argument for :class:`CornerRegion`.

    The frame is always the normalized artifact, by the same rule that shapes
    the database (`only_a_surface_marks_the_original`), so there is no
    ``representation`` field to get wrong. The bounding box is optional in both
    vocabularies' terms: §15's ``rough_cut`` is a whole-edge property whose
    honest spatial claim is "this edge" (#184).

    Raises:
        InvalidConditionAssessment: If the label is a bare string, or the
            severity contradicts it — `clean` and `unknown` carry none, every
            defect label must carry one.
    """

    label: CornerLabel | EdgeLabel
    confidence: Confidence
    severity: DefectSeverity | None = None
    bounding_box: BoundingBox | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.label, (CornerLabel, EdgeLabel)):
            raise InvalidConditionAssessment(
                f"a finding label must be a CornerLabel or EdgeLabel member, got {self.label!r} "
                "— a bare string is ambiguous, since several labels are in both vocabularies"
            )
        _validated_confidence(self.confidence, owner="a finding's")
        if self.label.value in NO_DEFECT_LABELS:
            if self.severity is not None:
                raise InvalidConditionAssessment(
                    f"a {self.label} finding asserts no ratable defect, "
                    f"but carries {self.severity!r}"
                )
        elif not isinstance(self.severity, DefectSeverity):
            raise InvalidConditionAssessment(
                f"a {self.label} finding must carry a severity (spec §17), got {self.severity!r}"
            )
        if self.bounding_box is not None and not isinstance(self.bounding_box, BoundingBox):
            raise InvalidConditionAssessment(
                f"a bounding box must be a BoundingBox, got {type(self.bounding_box).__name__}"
            )


_RATIO_FIELDS: Final = ("front_horizontal", "front_vertical", "back_horizontal", "back_vertical")


@dataclass(frozen=True, slots=True)
class Centering:
    """Spec §13's centering block: four ratios and a confidence.

    Ratios, never only qualitative labels (§13's own words). A ratio the
    template does not support — a borderless axis, §21 — is
    :class:`~tcg_domain.confidence.InsufficientInformation` per ratio, the
    in-process form of the database's per-axis nullable columns. At least one
    ratio must be measured: a confidence over zero measurements is a confidence
    about nothing, and the whole-axis refusal is spelled
    ``centering=INSUFFICIENT_INFORMATION`` on the assessment instead.

    Raises:
        InvalidConditionAssessment: If a measured ratio is outside ``[0, 1]``
            or nothing at all was measured.
    """

    front_horizontal: Uncertain[float]
    front_vertical: Uncertain[float]
    back_horizontal: Uncertain[float]
    back_vertical: Uncertain[float]
    confidence: Confidence

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        measured = 0
        for name in _RATIO_FIELDS:
            ratio = getattr(self, name)
            if isinstance(ratio, InsufficientInformation):
                continue
            set_field(self, name, _validated_fraction(ratio, label=name))
            measured += 1
        if measured == 0:
            raise InvalidConditionAssessment(
                "centering with nothing measured is the axis refusal — spell it "
                "centering=INSUFFICIENT_INFORMATION on the assessment"
            )
        _validated_confidence(self.confidence, owner="centering's")


_NOTHING_REFUSED: Final[Mapping[SurfaceLabel, InsufficientInformation]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class SurfaceAssessment:
    """What surface analysis concluded for one side — spec §16.

    A clean surface is an **empty findings tuple**: §16 has no `clean` class,
    and the asymmetry with corners and edges is the specification's own.
    ``not_assessed`` is where ADR 0010's fine classes are answered
    ``insufficient_information`` class-level rather than silently omitted
    (#185) — a model reporting fine scratches it cannot see is the
    confidently-wrong output §2.7 forbids.

    Args:
        findings: The defects found, surface vocabulary only, all on one side.
        not_assessed: Classes the analyzer refuses to answer for, each with the
            refusal carrying its reason. A class cannot be both found and
            refused.

    Raises:
        InvalidConditionAssessment: If a finding is not a surface defect, the
            findings span sides, or a class is both found and refused.
    """

    findings: tuple[Defect, ...]
    not_assessed: Mapping[SurfaceLabel, InsufficientInformation] = _NOTHING_REFUSED

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        findings = tuple(self.findings)
        for finding in findings:
            if not isinstance(finding, Defect):
                raise InvalidConditionAssessment(
                    f"a surface finding must be a Defect, got {type(finding).__name__}"
                )
            if not isinstance(finding.type, SurfaceLabel):
                raise InvalidConditionAssessment(
                    f"a surface finding must speak the surface vocabulary, got {finding.type!r}"
                )
        if len({finding.side for finding in findings}) > 1:
            raise InvalidConditionAssessment(
                "one surface assessment describes one photograph, but the findings span sides"
            )
        set_field(self, "findings", findings)

        refused: dict[SurfaceLabel, InsufficientInformation] = {}
        for key, verdict in self.not_assessed.items():
            label = _coerced(SurfaceLabel, key)
            if not isinstance(verdict, InsufficientInformation):
                raise InvalidConditionAssessment(
                    f"a refusal must be InsufficientInformation, got {type(verdict).__name__}"
                )
            refused[label] = verdict
        found = {finding.type for finding in findings}
        contradicted = sorted(label.value for label in found & set(refused))
        if contradicted:
            raise InvalidConditionAssessment(
                f"a class cannot be both found and not assessed: {contradicted}"
            )
        set_field(self, "not_assessed", MappingProxyType(refused))


def _validated_axis[RegionT: StrEnum, LabelT: StrEnum](
    mapping: Mapping[ImageSide, Uncertain[Mapping[RegionT, RegionFinding]]],
    *,
    regions: type[RegionT],
    labels: type[LabelT],
    axis: str,
) -> Mapping[ImageSide, Uncertain[Mapping[RegionT, RegionFinding]]]:
    """Freeze one corner/edge axis, checking totality and vocabulary.

    Totality is `image_quality`'s completeness rule: a region nobody assessed
    cannot be silently dropped — `unknown` with a confidence is how an analyzer
    says it could not judge one, and a whole side that could not run is the
    refusal.
    """
    if not isinstance(mapping, Mapping) or set(mapping) != set(V1_SIDES):
        raise InvalidConditionAssessment(
            f"{axis} must carry exactly the V1 sides {sorted(side.value for side in V1_SIDES)}"
        )
    frozen: dict[ImageSide, Uncertain[Mapping[RegionT, RegionFinding]]] = {}
    for side in V1_SIDES:
        answer = mapping[side]
        if isinstance(answer, InsufficientInformation):
            frozen[side] = answer
            continue
        if not isinstance(answer, Mapping) or set(answer) != set(regions):
            raise InvalidConditionAssessment(
                f"an answered {axis} side must carry every region exactly once "
                f"({sorted(region.value for region in regions)})"
            )
        for region in regions:
            finding = answer[region]
            if not isinstance(finding, RegionFinding):
                raise InvalidConditionAssessment(
                    f"{axis} findings must be RegionFinding, got {type(finding).__name__}"
                )
            if not isinstance(finding.label, labels):
                raise InvalidConditionAssessment(
                    f"a {axis[:-1]} finding must speak the {labels.__name__} vocabulary, "
                    f"got {finding.label!r} at {side.value} {region.value}"
                )
        frozen[side] = MappingProxyType({region: answer[region] for region in regions})
    return MappingProxyType(frozen)


def _validated_manufacturing_defects(value: object) -> Uncertain[tuple[Defect, ...]]:
    """Freeze the derived member, refusing anything but defects or the refusal.

    Takes ``object`` so the runtime guards stay reachable — the field's type is
    for callers, not a promise about what arrives (`_validated_measurement`'s
    pattern in `image_quality.py`).
    """
    if isinstance(value, InsufficientInformation):
        return value
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raise InvalidConditionAssessment(
            f"manufacturing defects must be a sequence of Defect or the refusal, "
            f"got {type(value).__name__}"
        )
    derived = tuple(value)
    for defect in derived:
        if not isinstance(defect, Defect):
            raise InvalidConditionAssessment(
                f"manufacturing defects must be Defect, got {type(defect).__name__}"
            )
        if defect.side not in V1_SIDES:
            # The other axes are total over V1_SIDES by construction; a derived
            # defect is the one place a side arrives on the value itself.
            raise InvalidConditionAssessment(
                f"a manufacturing defect names {defect.side.value}, but the V1 "
                f"assessment composes {sorted(side.value for side in V1_SIDES)} only"
            )
    return derived


# ----------------------------------------------------------------
# The persisted form — #187, `QualityReport.as_record()`'s sibling
# ----------------------------------------------------------------


def _refusal_record(refusal: InsufficientInformation) -> dict[str, object]:
    """Every refusal in the tree serializes as this same one-key object.

    A reader discriminates an answer from a refusal by the key's presence and
    nothing else, so the shape is identical at the axis, the ratio and the
    class level — and the reasonless case carries ``None`` rather than
    dropping the key.
    """
    return {"insufficient_information": refusal.reason}


def _box_record(box: BoundingBox) -> dict[str, object]:
    return {"x": box.x, "y": box.y, "width": box.width, "height": box.height}


def _defect_record(defect: Defect) -> dict[str, object]:
    # ponytail: `metadata` passes through unvalidated — §17's open bag is typed
    # `Mapping[str, object]`, so an analyzer putting a non-JSON value there
    # fails at the database driver, far from the cause. No analyzer emits
    # metadata today; validate here if one ever does.
    return {
        "type": str(defect.type),
        "confidence": defect.confidence.value,
        **({} if defect.severity is None else {"severity": str(defect.severity)}),
        "side": str(defect.side),
        "representation": str(defect.representation),
        **(
            {}
            if defect.bounding_box is None
            else {"bounding_box": _box_record(defect.bounding_box)}
        ),
        **(
            {} if defect.polygon is None else {"polygon": [list(point) for point in defect.polygon]}
        ),
        **({} if not defect.metadata else {"metadata": dict(defect.metadata)}),
    }


def _finding_record(finding: RegionFinding) -> dict[str, object]:
    return {
        "label": str(finding.label),
        "confidence": finding.confidence.value,
        **({} if finding.severity is None else {"severity": str(finding.severity)}),
        **(
            {}
            if finding.bounding_box is None
            else {"bounding_box": _box_record(finding.bounding_box)}
        ),
    }


def _centering_record(centering: Centering) -> dict[str, object]:
    record: dict[str, object] = {}
    for name in _RATIO_FIELDS:
        ratio = getattr(centering, name)
        record[name] = (
            _refusal_record(ratio) if isinstance(ratio, InsufficientInformation) else ratio
        )
    record["confidence"] = centering.confidence.value
    return record


def _axis_record[RegionT: StrEnum](
    axis: Mapping[ImageSide, Uncertain[Mapping[RegionT, RegionFinding]]],
) -> dict[str, object]:
    return {
        str(side): (
            _refusal_record(answer)
            if isinstance(answer, InsufficientInformation)
            else {str(region): _finding_record(finding) for region, finding in answer.items()}
        )
        for side, answer in axis.items()
    }


def _surface_record(surface: Mapping[ImageSide, Uncertain[SurfaceAssessment]]) -> dict[str, object]:
    return {
        str(side): (
            _refusal_record(answer)
            if isinstance(answer, InsufficientInformation)
            else {
                "findings": [_defect_record(finding) for finding in answer.findings],
                "not_assessed": {
                    str(label): _refusal_record(verdict)
                    for label, verdict in answer.not_assessed.items()
                },
            }
        )
        for side, answer in surface.items()
    }


@dataclass(frozen=True, slots=True)
class ConditionAssessment:
    """Spec §13's tree — the one neutral condition representation.

    This is what every grading-company model consumes and the type
    ``predict_grade``'s ``object`` parameter was left open for. It carries no
    company vocabulary, no grade and no score, and a refused axis leaves the
    assessment usable: M8's models take partial evidence and their
    distributions widen (#186) — one missing axis must not become a missing
    assessment, so even an assessment with every axis refused is
    constructible.

    Args:
        centering: The four ratios, or the whole axis refused.
        corners: Per V1 side, all four corners — spec §14's eight, keyed by
            :class:`ImageSide` and :class:`CornerRegion` rather than prefixed
            names, for :mod:`tcg_domain.annotation`'s reason.
        edges: Per V1 side, all four edges — spec §15's "represent front/back
            separately".
        surface: Per V1 side, the findings and class-level refusals.
        manufacturing_defects: Derived by composition from surface and edge
            findings (epic #8, decision 3) — this is only its home.
        eye_appeal: §13 names it; nothing in V1 defines or measures it, so the
            only representable answer is the refusal (epic #8, decision 2). A
            later version that can measure it widens this type.
        confidence: The assessment's overall confidence — how it is derived
            from the axes is the composer's business (#186), not a rule here.

    Raises:
        InvalidConditionAssessment: If a mapping is not total over the V1
            sides or the four regions, a finding speaks the wrong vocabulary,
            a surface defect disagrees with its side key, or ``eye_appeal``
            claims an answer.
    """

    centering: Uncertain[Centering]
    corners: Mapping[ImageSide, Uncertain[Mapping[CornerRegion, RegionFinding]]]
    edges: Mapping[ImageSide, Uncertain[Mapping[EdgeRegion, RegionFinding]]]
    surface: Mapping[ImageSide, Uncertain[SurfaceAssessment]]
    manufacturing_defects: Uncertain[tuple[Defect, ...]]
    eye_appeal: InsufficientInformation
    confidence: Confidence

    def __post_init__(self) -> None:
        set_field = object.__setattr__
        if not isinstance(self.centering, (Centering, InsufficientInformation)):
            raise InvalidConditionAssessment(
                f"centering must be Centering or the refusal, got {type(self.centering).__name__}"
            )
        set_field(
            self,
            "corners",
            _validated_axis(self.corners, regions=CornerRegion, labels=CornerLabel, axis="corners"),
        )
        set_field(
            self,
            "edges",
            _validated_axis(self.edges, regions=EdgeRegion, labels=EdgeLabel, axis="edges"),
        )

        if not isinstance(self.surface, Mapping) or set(self.surface) != set(V1_SIDES):
            raise InvalidConditionAssessment(
                f"surface must carry exactly the V1 sides {sorted(side.value for side in V1_SIDES)}"
            )
        surface: dict[ImageSide, Uncertain[SurfaceAssessment]] = {}
        for side in V1_SIDES:
            answer = self.surface[side]
            if isinstance(answer, InsufficientInformation):
                surface[side] = answer
                continue
            if not isinstance(answer, SurfaceAssessment):
                raise InvalidConditionAssessment(
                    f"a surface answer must be a SurfaceAssessment or the refusal, "
                    f"got {type(answer).__name__}"
                )
            disagreeing = sorted(
                {finding.side.value for finding in answer.findings if finding.side is not side}
            )
            if disagreeing:
                raise InvalidConditionAssessment(
                    f"the {side.value} surface carries findings for {disagreeing}"
                )
            surface[side] = answer
        set_field(self, "surface", MappingProxyType(surface))

        set_field(
            self,
            "manufacturing_defects",
            _validated_manufacturing_defects(self.manufacturing_defects),
        )

        if not isinstance(self.eye_appeal, InsufficientInformation):
            raise InvalidConditionAssessment(
                "eye_appeal is insufficient_information in V1: nothing defines or measures "
                f"it, so nothing may claim to have — got {type(self.eye_appeal).__name__}"
            )
        _validated_confidence(self.confidence, owner="the assessment's")

    def as_record(self) -> dict[str, object]:
        """The form persisted to `analyses.condition_details` (#187).

        Plain JSON-compatible types, so the caller writes it to a JSONB column
        without a serializer knowing anything about this package —
        `QualityReport.as_record()`'s rule. Vocabulary is ``str(member)``,
        confidences are floats, absent optionals are absent keys, and every
        refusal — an axis, a ratio, a class — is the same one-key
        ``{"insufficient_information": <reason or None>}`` object. The record
        deliberately carries **no version and no thresholds**: those are the
        composer's and the analyzers', recorded by the caller beside the
        document (#186's rule that an assessment carries none of them).
        """
        manufacturing = self.manufacturing_defects
        return {
            "centering": (
                _refusal_record(self.centering)
                if isinstance(self.centering, InsufficientInformation)
                else _centering_record(self.centering)
            ),
            "corners": _axis_record(self.corners),
            "edges": _axis_record(self.edges),
            "surface": _surface_record(self.surface),
            "manufacturing_defects": (
                _refusal_record(manufacturing)
                if isinstance(manufacturing, InsufficientInformation)
                else [_defect_record(defect) for defect in manufacturing]
            ),
            "eye_appeal": _refusal_record(self.eye_appeal),
            "confidence": self.confidence.value,
        }


def card_frame_of(details: Mapping[str, object] | None) -> BoundingBox | None:
    """The card's inner rectangle, from one artifact's stored record.

    #182's rule made a shared function: the frame is derived from the
    artifact's **stored** `normalization_details` — never from the
    normalization package's current thresholds — so it is right for whatever
    version produced that artifact. A record with no margin keys is a
    pre-#194 artifact whose card really does reach the edges (the whole unit
    square); a record with no dimensions, or one whose margins leave no card,
    derives no frame at all — the caller's refusal path.
    """
    if details is None:
        return None
    width = details.get("width")
    height = details.get("height")
    # `bool` is an `int`, and a zero or negative dimension is a corrupt record
    # — either would otherwise crash a worker job rather than take this
    # function's documented refusal path.
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    if width <= 0 or height <= 0:
        return None
    thresholds = details.get("thresholds")
    margin_mm = 0.0
    pixels_per_mm = 0.0
    if isinstance(thresholds, Mapping):
        raw_margin = thresholds.get("normalization_margin_mm")
        raw_ppm = thresholds.get("normalization_pixels_per_mm")
        if isinstance(raw_margin, (int, float)) and isinstance(raw_ppm, (int, float)):
            margin_mm = float(raw_margin)
            pixels_per_mm = float(raw_ppm)
    margin = margin_mm * pixels_per_mm
    try:
        return BoundingBox(
            x=margin / float(width),
            y=margin / float(height),
            width=(float(width) - 2 * margin) / float(width),
            height=(float(height) - 2 * margin) / float(height),
        )
    except InvalidConditionAssessment:
        return None
