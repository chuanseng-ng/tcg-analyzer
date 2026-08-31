"""The predicted-to-annotated match rule, stated as versioned constants.

#188's issue requires the IoU-style rule to be "a constant, versioned" —
`IOU_THRESHOLD` is part of `EVALUATION_VERSION`'s contract, and changing it
bumps that version rather than silently re-scoring history. Both sides'
coordinates are fractions of the *same* declared frame by the time they reach
this module: the caller filters by `representation` (#175's rule — the two
frames relate by a projective warp and are never converted), so this module
compares boxes and labels and knows nothing about frames.

A truth marker without a box is still truth — coordinates need an artifact,
the marker does not (#160) — so a boxless side degrades the rule to
label-only matching rather than disqualifying the marker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tcg_domain.condition import BoundingBox

__all__ = ["IOU_THRESHOLD", "Matching", "iou", "match_findings"]

#: Minimum intersection-over-union for two boxed findings to be the same
#: defect. 0.5 is the detection literature's conventional floor; nothing about
#: this corpus has yet argued for another value, and moving it is a version
#: bump, never a quiet edit.
IOU_THRESHOLD: Final = 0.5

#: One finding, as the matcher sees it: a label and maybe a box.
type LabelledBox = tuple[str, BoundingBox | None]


@dataclass(frozen=True, slots=True)
class Matching:
    """Which predicted findings were the annotated ones.

    Args:
        pairs: ``(predicted_index, truth_index)`` pairs, each side used at
            most once. Unmatched predictions are false positives and unmatched
            truth rows false negatives — the caller counts them, because only
            the caller knows the label universe it is scoring.
    """

    pairs: tuple[tuple[int, int], ...]


def iou(a: BoundingBox, b: BoundingBox) -> float:
    """Intersection over union of two unit-square boxes."""
    left = max(a.x, b.x)
    top = max(a.y, b.y)
    right = min(a.x + a.width, b.x + b.width)
    bottom = min(a.y + a.height, b.y + b.height)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = a.width * a.height + b.width * b.height - intersection
    return intersection / union


def match_findings(
    *, predicted: Sequence[LabelledBox], truth: Sequence[LabelledBox]
) -> Matching:
    """Greedily pair findings, best overlap first, labels always agreeing.

    Boxed-vs-boxed pairs qualify at `IOU_THRESHOLD` or better; a pair where
    either side lacks a box qualifies on the label alone, ranked below every
    boxed pair so real overlap is never displaced by a label coincidence.
    """
    candidates: list[tuple[float, int, int]] = []
    for p, (predicted_label, predicted_box) in enumerate(predicted):
        for t, (truth_label, truth_box) in enumerate(truth):
            if predicted_label != truth_label:
                continue
            if predicted_box is None or truth_box is None:
                candidates.append((0.0, p, t))
            else:
                overlap = iou(predicted_box, truth_box)
                if overlap >= IOU_THRESHOLD:
                    candidates.append((overlap, p, t))

    pairs: list[tuple[int, int]] = []
    matched_predictions: set[int] = set()
    matched_truth: set[int] = set()
    # Sorted by overlap descending, then by index for a deterministic order.
    for _, p, t in sorted(candidates, key=lambda entry: (-entry[0], entry[1], entry[2])):
        if p in matched_predictions or t in matched_truth:
            continue
        pairs.append((p, t))
        matched_predictions.add(p)
        matched_truth.add(t)

    return Matching(pairs=tuple(sorted(pairs)))
