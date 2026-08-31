"""The dataset manifest, parsed — the one seam between `ml/*` and the corpus.

ADR 0009: `ml/*` stays pure and reads a manifest, not the database. This
module is that reading. It takes the text of a committed
`datasets/manifests/*.json` file (rendered by `tcg-publish-dataset-version`)
and returns typed members carrying their annotation rows — the truth
`ml/evaluation` scores against, which #157 pre-authorized as fields on the
member and #188 landed there.

A file rendered before the annotation fields existed is refused rather than
read as an unannotated corpus: silence would score every image as clean,
which is exactly the fabricated certainty this package refuses elsewhere.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from tcg_domain.annotation import AnnotationKind
from tcg_domain.condition import BoundingBox, Representation
from tcg_domain.dataset import DatasetSplit

__all__ = [
    "CorpusAnnotation",
    "CorpusCentering",
    "CorpusMember",
    "EvaluationCorpus",
    "load_manifest",
]


@dataclass(frozen=True, slots=True)
class CorpusAnnotation:
    """One annotation row, as the manifest carries it."""

    id: uuid.UUID
    kind: AnnotationKind
    region: str | None
    label: str
    severity: str | None
    confidence: float
    bbox: BoundingBox | None
    representation: Representation
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CorpusCentering:
    """One centering measurement, as the manifest carries it."""

    id: uuid.UUID
    horizontal: float | None
    vertical: float | None
    confidence: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CorpusMember:
    """One image of the corpus: identifiers, split, and its truth rows."""

    training_image_id: uuid.UUID
    sha256: str
    split: DatasetSplit
    side: str
    source: str
    acquisition_method: str
    original_uri: str
    annotations: tuple[CorpusAnnotation, ...]
    centering: tuple[CorpusCentering, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCorpus:
    """A dataset version, as this package sees it."""

    dataset_version: str
    split_seed: int
    members: tuple[CorpusMember, ...]


def load_manifest(text: str) -> EvaluationCorpus:
    """Parse a rendered manifest.

    Raises:
        ValueError: For an empty membership, or a file rendered before the
            annotation fields existed — regenerate it with
            ``tcg-publish-dataset-version --regenerate`` first.
    """
    payload = json.loads(text)
    entries = payload["members"]
    if not entries:
        raise ValueError(f"{payload['dataset_version']} has no members; nothing to score")
    for entry in entries:
        if "annotations" not in entry or "centering" not in entry:
            raise ValueError(
                f"{payload['dataset_version']} was rendered before the manifest carried "
                f"annotation rows; regenerate it with tcg-publish-dataset-version "
                f"--regenerate before scoring"
            )

    return EvaluationCorpus(
        dataset_version=payload["dataset_version"],
        split_seed=payload["split_seed"],
        members=tuple(_member(entry) for entry in entries),
    )


def _member(entry: dict[str, object]) -> CorpusMember:
    return CorpusMember(
        training_image_id=uuid.UUID(str(entry["training_image_id"])),
        sha256=str(entry["sha256"]),
        split=DatasetSplit(str(entry["split"])),
        side=str(entry["side"]),
        source=str(entry["source"]),
        acquisition_method=str(entry["acquisition_method"]),
        original_uri=str(entry["original_uri"]),
        annotations=tuple(_annotation(marker) for marker in entry["annotations"]),  # type: ignore[union-attr]
        centering=tuple(_centering(measurement) for measurement in entry["centering"]),  # type: ignore[union-attr]
    )


def _annotation(marker: dict[str, object]) -> CorpusAnnotation:
    bbox = marker.get("bbox")
    return CorpusAnnotation(
        id=uuid.UUID(str(marker["id"])),
        kind=AnnotationKind(str(marker["kind"])),
        region=str(marker["region"]) if "region" in marker else None,
        label=str(marker["label"]),
        severity=str(marker["severity"]) if "severity" in marker else None,
        confidence=float(marker["confidence"]),  # type: ignore[arg-type]
        bbox=(
            BoundingBox(
                x=float(bbox["x"]),  # type: ignore[index]
                y=float(bbox["y"]),  # type: ignore[index]
                width=float(bbox["width"]),  # type: ignore[index]
                height=float(bbox["height"]),  # type: ignore[index]
            )
            if bbox is not None
            else None
        ),
        representation=Representation(str(marker["representation"])),
        created_at=datetime.fromisoformat(str(marker["created_at"])),
    )


def _centering(measurement: dict[str, object]) -> CorpusCentering:
    return CorpusCentering(
        id=uuid.UUID(str(measurement["id"])),
        horizontal=(
            float(measurement["horizontal"]) if "horizontal" in measurement else None  # type: ignore[arg-type]
        ),
        vertical=(
            float(measurement["vertical"]) if "vertical" in measurement else None  # type: ignore[arg-type]
        ),
        confidence=float(measurement["confidence"]),  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(str(measurement["created_at"])),
    )
