"""The vocabulary a training image is annotated with — spec §30.

Spec §30 names eleven things the annotation tool must capture, and four of them
are vocabularies rather than free text: which kind of thing is being annotated,
where on the card it is, what it is, and how bad it is. Those four are here.
The other seven — the viewer, zoom, front/back, the coordinates, the
uncertainty, the annotator and the timestamp — are columns, a viewer control or
`training_images.side`, and none of them is a closed list.

**Here rather than in `tcg_api.datasets.tables` for the reason
:mod:`tcg_domain.dataset` gives**: two things need these words from opposite
sides of the service boundary. The CHECK constraints on `annotations` are one;
M7's condition representation and M8's training readers are the other, and a
reader that had to import `services/api` to name a corner would drag FastAPI and
SQLAlchemy into an ml package for the sake of eight strings.

Three things about these lists are the specification's rather than this
project's, and should not be tidied:

* **§16's surface classes contain no `clean`**, where §14's corners and §15's
  edges both do. A surface with nothing wrong is a surface nobody annotated; a
  corner inspected and found sound is a corner annotation saying so. The
  asymmetry is why :data:`NO_DEFECT_LABELS` is a set of two rather than a rule
  about the first member of each list.
* **§14 lists its eight corners front- and back-prefixed.** The prefix is
  `training_images.side` — the image already knows which face it shows, and
  repeating it on the annotation would let the two disagree. :class:`CornerRegion`
  is therefore four members, not eight, and the same argument applies to §15's
  "represent front/back separately".
* **Every list ends in `unknown`.** That is spec §2.7's uncertainty inside the
  vocabulary rather than beside it: an annotator who can see damage but cannot
  name it records where it is and says so, which is a usable training signal
  where a forced guess is not.

Members are `str`, as :class:`~tcg_domain.dataset.DatasetSplit`'s are, so the
schema stores the value and never the member's repr.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "LABELS_BY_KIND",
    "NO_DEFECT_LABELS",
    "REGIONS_BY_KIND",
    "AnnotationKind",
    "CornerLabel",
    "CornerRegion",
    "DefectSeverity",
    "EdgeLabel",
    "EdgeRegion",
    "SurfaceLabel",
]


class AnnotationKind(StrEnum):
    """What is being annotated — §30's corner, edge and surface annotation.

    Three members and not four: §30's centering measurements are a *measurement*
    and these are *markers*, they share no field beyond the annotator and the
    time, and `centering_measurements` is therefore its own table. A fourth
    member here would be a row whose label, severity and bounding box were all
    NULL by construction.
    """

    CORNER = "corner"
    EDGE = "edge"
    SURFACE = "surface"


class CornerRegion(StrEnum):
    """Which corner — spec §14, without the side prefix.

    Reading order, which is also the order §14 lists them within a side.
    """

    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


class EdgeRegion(StrEnum):
    """Which edge — spec §15, clockwise from the top.

    §15 names no positions at all; it says only to represent front and back
    separately. Four edges is this project's, and clockwise from the top is
    :data:`~tcg_domain.card_geometry.CORNER_NAMES`' order, so a reader who knows
    one knows the other.
    """

    TOP = "top"
    RIGHT = "right"
    BOTTOM = "bottom"
    LEFT = "left"


class CornerLabel(StrEnum):
    """Spec §14's potential corner labels, in the specification's order."""

    CLEAN = "clean"
    WHITENING = "whitening"
    ROUNDING = "rounding"
    CHIPPING = "chipping"
    DENT = "dent"
    CREASE = "crease"
    LAYERING = "layering"
    UNKNOWN = "unknown"


class EdgeLabel(StrEnum):
    """Spec §15's potential edge labels, in the specification's order.

    Deliberately **not** :class:`CornerLabel`: §15 adds `rough_cut` and
    `notching`, which are cutting defects a corner does not have, and drops
    `rounding` and `crease`, which are not edge failures. Collapsing the two into
    one list would make a corner annotable as `rough_cut`.
    """

    CLEAN = "clean"
    WHITENING = "whitening"
    CHIPPING = "chipping"
    ROUGH_CUT = "rough_cut"
    NOTCHING = "notching"
    LAYERING = "layering"
    DENT = "dent"
    UNKNOWN = "unknown"


class SurfaceLabel(StrEnum):
    """Spec §16's potential surface classes, in the specification's order.

    Twelve, and **no `clean`** — see the module docstring. Where a corner is
    annotated once and may be found sound, a surface carries one annotation per
    defect found and none at all when there are none.
    """

    SCRATCH = "scratch"
    PRINT_LINE = "print_line"
    DENT = "dent"
    INDENTATION = "indentation"
    STAIN = "stain"
    SCUFF = "scuff"
    PRINT_DOT = "print_dot"
    COLOR_ISSUE = "color_issue"
    REGISTRATION_ISSUE = "registration_issue"
    GLOSS_ISSUE = "gloss_issue"
    FACTORY_DEFECT = "factory_defect"
    UNKNOWN = "unknown"


class DefectSeverity(StrEnum):
    """How bad a defect is — spec §17's `severity`, which §17 does not define.

    An ordinal rather than a number in ``[0, 1]``, and that is a decision about
    who is answering. There is one annotator and no inter-annotator agreement
    study (§30's feature list has neither), so a continuous scale would record a
    precision nobody could reproduce — and a model fitting that precision fits
    noise. Three levels are reproducible; M8 may map them to numbers, which is a
    modelling choice made where the model lives.
    """

    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


#: The labels each kind admits. The CHECK on `annotations` is composed from this,
#: so a label added to one of the specification's lists cannot become one the
#: database silently refuses.
LABELS_BY_KIND: Final[Mapping[AnnotationKind, frozenset[str]]] = MappingProxyType(
    {
        AnnotationKind.CORNER: frozenset(label.value for label in CornerLabel),
        AnnotationKind.EDGE: frozenset(label.value for label in EdgeLabel),
        AnnotationKind.SURFACE: frozenset(label.value for label in SurfaceLabel),
    }
)

#: The regions each kind admits. Surface is **empty rather than absent**: a
#: surface defect's position is its bounding box, so the mapping stays total and
#: the constraint reading it needs no special case for a missing key.
REGIONS_BY_KIND: Final[Mapping[AnnotationKind, frozenset[str]]] = MappingProxyType(
    {
        AnnotationKind.CORNER: frozenset(region.value for region in CornerRegion),
        AnnotationKind.EDGE: frozenset(region.value for region in EdgeRegion),
        AnnotationKind.SURFACE: frozenset(),
    }
)

#: The two labels that assert no defect, and therefore the two that carry no
#: severity — `clean` found nothing to rate, `unknown` could not rate what it
#: found. Every other label names a defect, and §17 requires a severity beside
#: one; `ck_annotations_a_defect_carries_a_severity` is that rule in SQL.
NO_DEFECT_LABELS: Final[frozenset[str]] = frozenset(
    {CornerLabel.CLEAN.value, CornerLabel.UNKNOWN.value}
)
