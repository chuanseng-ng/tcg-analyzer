"""The annotation protocol, executable — how manifest rows become truth.

Three rules, all decided during #181 and binding on every reader
(`datasets/schemas/annotation-schema.md` and the M7 notes):

* **The newest row per `(kind, region)` is the current view** for corners and
  edges — the tables are append-only, so a correction is a new row and the
  older one must not double-count. Surface rows are *never* collapsed: a
  surface has as many defects as it has rows, and no region distinguishes a
  correction from a second stain.
* **Absence is clean, on worked-on images only.** An image carrying at least
  one row across the two tables has been examined; a region with no marker on
  it is clean at unstated confidence. An image with no rows at all has not
  been examined, and the absence rule must never touch it.
* **A reader filters by declared frame and never converts** (#175): the
  normalized artifact and the original photograph relate by a projective
  warp, so a fraction of one means nothing in the other.

And one for the target (#220): **the newest outcome per company is the
grade that company issued.** A copy cracked and resubmitted to the same
company has one current slab; two companies are two answers, never merged.
"""

from __future__ import annotations

from tcg_domain.annotation import AnnotationKind
from tcg_domain.condition import Representation

from tcg_ml_evaluation.grading import IssuedGrade
from tcg_ml_evaluation.manifest import CorpusAnnotation, CorpusCentering, CorpusMember

__all__ = ["current_view", "is_worked_on", "issued_grades", "newest_centering", "surface_truth"]


def is_worked_on(member: CorpusMember) -> bool:
    """Whether the absence-is-clean rule may be applied to this image."""
    return bool(member.annotations) or bool(member.centering)


def current_view(
    member: CorpusMember,
) -> dict[tuple[AnnotationKind, str], CorpusAnnotation]:
    """The newest corner and edge row per region — surface rows excluded."""
    view: dict[tuple[AnnotationKind, str], CorpusAnnotation] = {}
    for marker in sorted(member.annotations, key=lambda row: (row.created_at, str(row.id))):
        if marker.kind is AnnotationKind.SURFACE or marker.region is None:
            continue
        view[(marker.kind, marker.region)] = marker
    return view


def surface_truth(
    member: CorpusMember, *, representation: Representation
) -> tuple[CorpusAnnotation, ...]:
    """Every surface row declaring the given frame, in row order."""
    return tuple(
        marker
        for marker in sorted(member.annotations, key=lambda row: (row.created_at, str(row.id)))
        if marker.kind is AnnotationKind.SURFACE and marker.representation is representation
    )


def issued_grades(member: CorpusMember) -> dict[str, IssuedGrade]:
    """What each company issued for this image's copy — the newest slab per company.

    The shape `GradeSubject.outcomes` takes, keyed by company slug.
    """
    grades: dict[str, IssuedGrade] = {}
    for outcome in sorted(member.grading_outcomes, key=lambda row: (row.created_at, str(row.id))):
        grades[outcome.company] = IssuedGrade(
            company=outcome.company, grade=outcome.grade, designation=outcome.designation
        )
    return grades


def newest_centering(member: CorpusMember) -> CorpusCentering | None:
    """The current centering reading, or ``None`` where none was recorded."""
    if not member.centering:
        return None
    return max(member.centering, key=lambda row: (row.created_at, str(row.id)))
