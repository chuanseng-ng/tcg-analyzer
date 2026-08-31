"""Compose spec §13's tree from the four axis analyzers — issue #186.

One :class:`~tcg_domain.condition.ConditionAssessment` assembled from
`ml/centering`, `ml/corners`, `ml/edges` and `ml/surface`, plus the two
members no analyzer produces. Composition, not re-analysis: nothing here
re-scores a finding, weighs one axis against another, or knows what any
grading company rewards — aggregation into a grade is exactly what §2.2
reserves for the company models (M8).

The two derived members:

- ``manufacturing_defects`` collects the findings that are manufacturing by
  nature — ``factory_defect``, ``registration_issue``, ``print_line`` and
  ``print_dot`` from surface, ``rough_cut`` from edges — and is the honest
  refusal when nothing was found *and* any feeding class went unassessed.
  The v0.1.0 surface baseline refuses all four of its feeding classes
  class-level, so in practice this member is ``insufficient_information``
  until a later surface version assesses them; it flips to real tuples with
  no change here.
- ``eye_appeal`` is ``insufficient_information`` always: §13 names it, no
  spec section defines it and §30's annotation features do not capture it,
  so nothing can train or verify it — fabricating it would violate §2.7.

The overall confidence is ``min`` over every confidence the answered
members carry — never a product, the economic engine's rule (#59/#64) for
the same reason: the assessment is no better than its weakest measured
claim. When no member carries a single confidence (both artifacts
undecodable, say) the composition refuses rather than inventing a number
for zero measurements — the one refusal path, and #91's rule that "not
measured" is never a figure.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Final

from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.annotation import CornerRegion, EdgeLabel, EdgeRegion, SurfaceLabel
from tcg_domain.condition import (
    BoundingBox,
    Centering,
    ConditionAssessment,
    Defect,
    RegionFinding,
    Representation,
    SurfaceAssessment,
)
from tcg_domain.confidence import Confidence, InsufficientInformation, Uncertain
from tcg_ml_centering import CENTERING_VERSION, centering_of, measure
from tcg_ml_corners import CORNERS_VERSION
from tcg_ml_corners import classify as classify_corners
from tcg_ml_edges import EDGES_VERSION
from tcg_ml_edges import classify as classify_edges
from tcg_ml_surface import SURFACE_VERSION
from tcg_ml_surface import classify as classify_surface

__all__ = ["CONDITION_VERSION", "assess", "compose"]

#: The version #187 records as the analysis's ``model_bundle_version``.
#: Composed from the axis constants rather than hand-maintained, so a
#: package bump cannot be forgotten (`PIPELINE_VERSION`'s rule); the own
#: leading component versions the composition logic itself — changing the
#: confidence rule or the manufacturing derivation bumps it even when no
#: axis moved.
CONDITION_VERSION: Final = "+".join(
    (
        "condition-compose-v0.1.0",
        CENTERING_VERSION,
        CORNERS_VERSION,
        EDGES_VERSION,
        SURFACE_VERSION,
    )
)

#: §16's classes that are manufacturing by nature — the issue's list. The
#: fifth feeder is edges' ``rough_cut``, handled in the derivation.
_MANUFACTURING_SURFACE_LABELS: Final = frozenset(
    (
        SurfaceLabel.FACTORY_DEFECT,
        SurfaceLabel.REGISTRATION_ISSUE,
        SurfaceLabel.PRINT_LINE,
        SurfaceLabel.PRINT_DOT,
    )
)

_EYE_APPEAL: Final = InsufficientInformation("eye_appeal_not_measured_in_v1")


def compose(
    *,
    centering: Uncertain[Centering],
    corners: Mapping[ImageSide, Uncertain[Mapping[CornerRegion, RegionFinding]]],
    edges: Mapping[ImageSide, Uncertain[Mapping[EdgeRegion, RegionFinding]]],
    surface: Mapping[ImageSide, Uncertain[SurfaceAssessment]],
) -> Uncertain[ConditionAssessment]:
    """Assemble the assessment from already-produced axis outputs.

    Public beside :func:`assess` on purpose: the derivations are only
    reachable with inputs the v0.1.0 analyzers never emit, and #188's
    benchmark replays stored outputs.
    """
    confidences = list(_confidences(centering, corners, edges, surface))
    if not confidences:
        return InsufficientInformation("no_axis_measured")
    return ConditionAssessment(
        centering=centering,
        corners=corners,
        edges=edges,
        surface=surface,
        manufacturing_defects=_manufacturing_defects(edges, surface),
        eye_appeal=_EYE_APPEAL,
        confidence=min(confidences),
    )


def assess(
    front: bytes,
    back: bytes,
    *,
    front_card_frame: BoundingBox,
    back_card_frame: BoundingBox,
) -> Uncertain[ConditionAssessment]:
    """Both sides' normalized artifacts in, one assessment out.

    The frames are per-side parameters because each artifact has its own
    stored ``normalization_details`` (#182's rule: the caller derives them
    from the record, never from the normalizer's current thresholds).
    Default thresholds only — a caller wanting custom ones runs the
    analyzers itself and hands :func:`compose` the outputs.
    """
    sides: dict[ImageSide, tuple[bytes, BoundingBox]] = {
        ImageSide.FRONT: (front, front_card_frame),
        ImageSide.BACK: (back, back_card_frame),
    }
    return compose(
        centering=centering_of(
            measure(front, card_frame=front_card_frame),
            measure(back, card_frame=back_card_frame),
        ),
        corners={
            side: classify_corners(data, card_frame=frame) for side, (data, frame) in sides.items()
        },
        edges={
            side: classify_edges(data, card_frame=frame) for side, (data, frame) in sides.items()
        },
        surface={
            side: classify_surface(data, side=side, card_frame=frame)
            for side, (data, frame) in sides.items()
        },
    )


def _confidences(
    centering: Uncertain[Centering],
    corners: Mapping[ImageSide, Uncertain[Mapping[CornerRegion, RegionFinding]]],
    edges: Mapping[ImageSide, Uncertain[Mapping[EdgeRegion, RegionFinding]]],
    surface: Mapping[ImageSide, Uncertain[SurfaceAssessment]],
) -> Iterator[Confidence]:
    """Every confidence the answered members carry.

    An ``unknown`` region's flat 0.5 counts — an honest drag on the whole.
    A clean surface contributes no number (a `SurfaceAssessment` carries no
    axis-level confidence), so on real artifacts the corner and edge
    findings are what guarantee the collection is non-empty whenever any
    side's bytes decode.
    """
    if isinstance(centering, Centering):
        yield centering.confidence
    for side in V1_SIDES:
        for findings in (corners[side], edges[side]):
            if not isinstance(findings, InsufficientInformation):
                for finding in findings.values():
                    yield finding.confidence
        face = surface[side]
        if isinstance(face, SurfaceAssessment):
            for defect in face.findings:
                yield defect.confidence


def _manufacturing_defects(
    edges: Mapping[ImageSide, Uncertain[Mapping[EdgeRegion, RegionFinding]]],
    surface: Mapping[ImageSide, Uncertain[SurfaceAssessment]],
) -> Uncertain[tuple[Defect, ...]]:
    """Collect the manufacturing-class findings; refuse when unassessed.

    A found defect is a found defect and always travels. Nothing found is an
    empty tuple only when every feeding class was actually assessed — a
    refused surface side, a feeding class in ``not_assessed`` or a refused
    edges side means "never looked", not "none there".

    ponytail: edges gives no class-level refusal signal, so its v0.1.0
    rough_cut blindness is invisible here — moot while surface's refusals
    trigger (always in v0.1.0); revisit when a surface version assesses its
    four classes.
    """
    found: list[Defect] = []
    unassessed = False
    for side in V1_SIDES:
        face = surface[side]
        if isinstance(face, InsufficientInformation):
            unassessed = True
        else:
            found.extend(
                defect for defect in face.findings if defect.type in _MANUFACTURING_SURFACE_LABELS
            )
            if any(label in face.not_assessed for label in _MANUFACTURING_SURFACE_LABELS):
                unassessed = True
        findings = edges[side]
        if isinstance(findings, InsufficientInformation):
            unassessed = True
        else:
            found.extend(
                Defect(
                    type=EdgeLabel.ROUGH_CUT,
                    confidence=finding.confidence,
                    severity=finding.severity,
                    side=side,
                    representation=Representation.NORMALIZED,
                    bounding_box=finding.bounding_box,
                )
                for finding in findings.values()
                if finding.label is EdgeLabel.ROUGH_CUT
            )
    if found:
        return tuple(found)
    if unassessed:
        return InsufficientInformation("manufacturing_classes_not_assessed")
    return ()
