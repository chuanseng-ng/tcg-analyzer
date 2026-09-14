"""Rebuilding a corpus database from its published manifests — #348.

`tcg_corpus` was lost with its volume on 2026-09-07. What survived is enough:
the committed manifests carry every image id, content hash, storage key, split,
annotation, centering measurement and grading outcome, and the original
photographs still hash to the manifests' `sha256`. This module puts the rows
back **with the ids and timestamps the manifests record**, so
`tcg-publish-dataset-version --regenerate` reproduces each file byte for byte.
That is the acceptance check, and the runbook is `docs/rebuilding-the-corpus.md`.

Three things are load-bearing:

* **Only into an empty corpus.** Every corpus table is checked inside the
  transaction that writes, so a rerun is a refusal and never a duplicate. That
  is also why this is not a second door into `dataset_members`
  (`versioning`'s "no member is added to an existing version"): it can only
  recreate versions into a database that has none.
* **A hash mismatch is a refusal, never a re-encode.** The digest is over the
  stored bytes, so a photograph that does not match is a different image.
  `validate_image` is deliberately not called: it re-saves the file, and the
  manifest's `sha256` is the stronger check.
* **Nothing is normalized here.** An annotation is a fraction of the artifact
  its annotator saw, so normalization runs afterwards at the detector version
  the annotations were drawn on, never HEAD. The runbook says how.

What the manifests do not carry is supplied by the operator: `physical_copy_id`
and `acquired_at` per photograph (the copies CSV), the licence and use rights
(checked by ingestion's `verify_provenance`), and an `annotator_id`. `notes`,
`metadata`, polygons, certification columns and BGS subgrades are not rendered,
so they do not come back.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from tcg_shared.storage import StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.ingestion import (
    ProvenanceRefused,
    TrainingImageProvenance,
    verify_provenance,
)
from tcg_api.datasets.tables import (
    centering_measurements,
    dataset_members,
    dataset_versions,
    grading_outcomes,
    image_annotations,
    physical_copies,
    training_images,
)
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = ["CopyRow", "Photograph", "RestoreRefused", "main", "read_copies", "restore"]

logger = logging.getLogger(__name__)

#: Every table a restore writes. One of them holding a row means the target is
#: not a lost corpus, and writing into it would duplicate or interleave.
_CORPUS_TABLES: Final = (
    physical_copies,
    training_images,
    image_annotations,
    centering_measurements,
    grading_outcomes,
    dataset_versions,
    dataset_members,
)

_COPIES_COLUMNS: Final = ("file", "physical_copy_id", "acquired_at")

#: The two types the corpus stores (#33). Anything else is not a stored original.
_STORED_TYPES: Final = {"PNG": "image/png", "JPEG": "image/jpeg"}


class RestoreRefused(ValueError):
    """The corpus cannot be rebuilt as asked. The message names what to fix."""


@dataclass(frozen=True, slots=True)
class CopyRow:
    """One line of the copies CSV: a photograph and the facts no manifest carries."""

    path: Path
    physical_copy_id: uuid.UUID
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class Photograph:
    """One original's bytes, and the copy and capture instant the operator supplied."""

    data: bytes
    physical_copy_id: uuid.UUID
    acquired_at: datetime


def read_copies(path: Path) -> tuple[CopyRow, ...]:
    """Parse the copies CSV (`file,physical_copy_id,acquired_at`), or say which row is wrong.

    `file` resolves against the CSV's own directory, as `tcg-ingest-training-batch`'s
    manifest does. `acquired_at` must name its offset.

    Raises:
        RestoreRefused: For a missing column, no rows, a missing file, a bad UUID or
            a naive timestamp.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise RestoreRefused(f"{path} cannot be read: {error}") from error

    reader = csv.DictReader(text.splitlines())
    missing = [name for name in _COPIES_COLUMNS if name not in (reader.fieldnames or ())]
    if missing:
        raise RestoreRefused(
            f"{path} needs the columns {','.join(_COPIES_COLUMNS)}; missing {', '.join(missing)}"
        )

    rows = []
    for number, entry in enumerate(reader, start=1):
        name = (entry["file"] or "").strip()
        file = path.parent / name
        if not name or not file.is_file():
            raise RestoreRefused(
                f"row {number} names {name!r}, which is not a file in {path.parent}"
            )
        try:
            copy = uuid.UUID((entry["physical_copy_id"] or "").strip())
        except ValueError:
            raise RestoreRefused(
                f"row {number}'s physical_copy_id {entry['physical_copy_id']!r} is not a UUID"
            ) from None
        try:
            when = datetime.fromisoformat((entry["acquired_at"] or "").strip())
        except ValueError:
            raise RestoreRefused(
                f"row {number}'s acquired_at {entry['acquired_at']!r} is not an ISO 8601 timestamp"
            ) from None
        if when.tzinfo is None:
            raise RestoreRefused(
                f"row {number}'s acquired_at names no time zone; write e.g. 2026-08-29T19:02:00+08:00"
            )
        rows.append(CopyRow(path=file, physical_copy_id=copy, acquired_at=when))

    if not rows:
        raise RestoreRefused(f"{path} lists no photographs")
    return tuple(rows)


async def restore(
    engine: AsyncEngine,
    storage: ObjectStorage,
    *,
    manifests: Sequence[Mapping[str, Any]],
    photographs: Sequence[Photograph],
    license: str | None,
    commercial_use_allowed: bool | None,
    derivative_use_allowed: bool | None,
    annotator_id: str,
) -> dict[str, int]:
    """Rebuild the rows and objects the manifests describe, in one transaction.

    Every refusal but the non-empty target happens before a connection or an
    object store is touched.

    Returns:
        How many rows of each kind were written, for the operator's log.

    Raises:
        RestoreRefused: If two manifests disagree about an image, a member has no
            matching photograph, a photograph matches no member, or the target
            database already holds corpus rows.
        ProvenanceRefused: If ADR 0008 does not admit a member's source with the
            rights given.
    """
    images: dict[str, Mapping[str, Any]] = {}
    annotations: dict[str, tuple[str, Mapping[str, Any]]] = {}
    centering: dict[str, tuple[str, Mapping[str, Any]]] = {}
    outcomes: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for manifest in manifests:
        for member in manifest["members"]:
            image_id = member["training_image_id"]
            seen = images.setdefault(image_id, member)
            if seen["sha256"] != member["sha256"]:
                raise RestoreRefused(
                    f"training image {image_id} has sha256 {seen['sha256']} in one manifest "
                    f"and {member['sha256']} in another; a manifest was edited by hand"
                )
            # Keyed by row id: the same row appears in every version it was read into.
            # An absent key is a file rendered before the field existed (#188,
            # #220), e.g. v0.1.0 has no `grading_outcomes`; a later manifest
            # carries the rows.
            for entry in member.get("annotations", ()):
                annotations.setdefault(entry["id"], (image_id, entry))
            for entry in member.get("centering", ()):
                centering.setdefault(entry["id"], (image_id, entry))
            for entry in member.get("grading_outcomes", ()):
                outcomes.setdefault(entry["id"], (image_id, entry))

    by_sha = {hashlib.sha256(photograph.data).hexdigest(): photograph for photograph in photographs}
    strangers = sorted(set(by_sha) - {member["sha256"] for member in images.values()})
    if strangers:
        raise RestoreRefused(
            f"{len(strangers)} photograph(s) match no manifest member ({', '.join(strangers)}). "
            f"The digest is over the stored bytes, so a re-encoded file is a different image."
        )
    missing = sorted(
        image_id for image_id, member in images.items() if member["sha256"] not in by_sha
    )
    if missing:
        raise RestoreRefused(
            f"{len(missing)} manifest member(s) have no photograph with a matching sha256: "
            f"{', '.join(missing)}"
        )

    for member in images.values():
        verify_provenance(
            TrainingImageProvenance(
                source=member["source"],
                acquisition_method=member["acquisition_method"],
                license=license,
                commercial_use_allowed=commercial_use_allowed,
                derivative_use_allowed=derivative_use_allowed,
                redistribution_allowed=False,
                acquired_at=by_sha[member["sha256"]].acquired_at,
            )
        )
    described = {sha: _describe(photograph.data) for sha, photograph in by_sha.items()}
    copy_of = {
        image_id: by_sha[member["sha256"]].physical_copy_id for image_id, member in images.items()
    }

    async with engine.begin() as connection:
        await _refuse_a_non_empty_target(connection)

        await connection.execute(
            sa.insert(physical_copies),
            [{"id": copy} for copy in sorted(set(copy_of.values()), key=str)],
        )
        await connection.execute(
            sa.insert(training_images),
            [
                {
                    "id": uuid.UUID(image_id),
                    "physical_copy_id": copy_of[image_id],
                    "side": member["side"],
                    "original_uri": member["original_uri"],
                    "sha256": member["sha256"],
                    "mime_type": described[member["sha256"]][0],
                    "width": described[member["sha256"]][1],
                    "height": described[member["sha256"]][2],
                    "source": member["source"],
                    "acquisition_method": member["acquisition_method"],
                    "license": license,
                    "commercial_use_allowed": commercial_use_allowed,
                    "derivative_use_allowed": derivative_use_allowed,
                    "redistribution_allowed": False,
                    "acquired_at": by_sha[member["sha256"]].acquired_at,
                }
                for image_id, member in images.items()
            ],
        )
        if annotations:
            await connection.execute(
                sa.insert(image_annotations),
                [
                    {
                        "id": uuid.UUID(row_id),
                        "training_image_id": uuid.UUID(image_id),
                        "kind": entry["kind"],
                        "region": entry.get("region"),
                        "label": entry["label"],
                        "severity": entry.get("severity"),
                        "confidence": entry["confidence"],
                        "bbox_x": entry.get("bbox", {}).get("x"),
                        "bbox_y": entry.get("bbox", {}).get("y"),
                        "bbox_width": entry.get("bbox", {}).get("width"),
                        "bbox_height": entry.get("bbox", {}).get("height"),
                        "representation": entry["representation"],
                        "annotator_id": annotator_id,
                        "created_at": datetime.fromisoformat(entry["created_at"]),
                    }
                    for row_id, (image_id, entry) in annotations.items()
                ],
            )
        if centering:
            await connection.execute(
                sa.insert(centering_measurements),
                [
                    {
                        "id": uuid.UUID(row_id),
                        "training_image_id": uuid.UUID(image_id),
                        "horizontal": entry.get("horizontal"),
                        "vertical": entry.get("vertical"),
                        "confidence": entry["confidence"],
                        "annotator_id": annotator_id,
                        "created_at": datetime.fromisoformat(entry["created_at"]),
                    }
                    for row_id, (image_id, entry) in centering.items()
                ],
            )
        if outcomes:
            await connection.execute(
                sa.insert(grading_outcomes),
                [
                    {
                        "id": uuid.UUID(row_id),
                        "physical_copy_id": copy_of[image_id],
                        "grading_company": entry["company"],
                        "certification_number": entry["certification_number"],
                        "grade": entry.get("grade"),
                        "designation": entry.get("designation"),
                        "created_at": datetime.fromisoformat(entry["created_at"]),
                    }
                    for row_id, (image_id, entry) in outcomes.items()
                ],
            )

        for manifest in sorted(manifests, key=lambda manifest: manifest["ordinal"]):
            version_id = uuid.UUID(manifest["id"])
            # `ordinal` is GENERATED ALWAYS so no *publish* can place a version out
            # of sequence. A restore is putting back the sequence that was, so it
            # overrides once per version and moves the identity past it below.
            await connection.execute(
                sa.text(
                    "INSERT INTO dataset_versions (id, ordinal, version, split_seed, created_at) "
                    "OVERRIDING SYSTEM VALUE "
                    "VALUES (:id, :ordinal, :version, :split_seed, :created_at)"
                ),
                {
                    "id": version_id,
                    "ordinal": manifest["ordinal"],
                    "version": manifest["dataset_version"],
                    "split_seed": manifest["split_seed"],
                    "created_at": datetime.fromisoformat(manifest["created_at"]),
                },
            )
            await connection.execute(
                sa.insert(dataset_members),
                [
                    {
                        "dataset_version_id": version_id,
                        "training_image_id": uuid.UUID(member["training_image_id"]),
                        "split": member["split"],
                    }
                    for member in manifest["members"]
                ],
            )
        await connection.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('dataset_versions', 'ordinal'), "
                "(SELECT max(ordinal) FROM dataset_versions))"
            )
        )

        # The rows first, ingestion's order: a storage failure rolls them back.
        # ponytail: objects put before a failed put or commit are left behind. The
        # keys are the manifest's, so a rerun overwrites them with the same bytes.
        for member in images.values():
            await storage.put(
                StorageKey(member["original_uri"]),
                by_sha[member["sha256"]].data,
                content_type=described[member["sha256"]][0],
            )

    return {
        "versions": len(manifests),
        "physical_copies": len(set(copy_of.values())),
        "training_images": len(images),
        "annotations": len(annotations),
        "centering_measurements": len(centering),
        "grading_outcomes": len(outcomes),
    }


def _describe(data: bytes) -> tuple[str, int, int]:
    """The stored original's type and dimensions, read from the header."""
    with Image.open(io.BytesIO(data)) as image:
        mime = _STORED_TYPES.get(image.format or "")
        width, height = image.size
    if mime is None:
        raise RestoreRefused(
            "an original is neither PNG nor JPEG, so it was never a stored original"
        )
    return mime, width, height


async def _refuse_a_non_empty_target(connection: AsyncConnection) -> None:
    occupied = [
        table.name
        for table in _CORPUS_TABLES
        if (await connection.execute(sa.select(sa.exists().select_from(table)))).scalar_one()
    ]
    if occupied:
        raise RestoreRefused(
            f"the target database is not empty ({', '.join(occupied)} hold rows). A restore "
            f"rebuilds a lost corpus; it never merges into a live one. Check "
            f"TCG_API_DATABASE_URL names the corpus database you mean."
        )


# ---------------------------------------------------------------------------
# The command line — `uv run tcg-restore-dataset-version`
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        action="append",
        required=True,
        metavar="FILE",
        help="a published manifest; repeat for every version to restore, in any order",
    )
    parser.add_argument(
        "--copies",
        type=Path,
        required=True,
        metavar="CSV",
        help="file,physical_copy_id,acquired_at — one row per original, paths relative to the CSV",
    )
    parser.add_argument("--license", help="what permits the use (ADR 0008 refuses a blank one)")
    parser.add_argument(
        "--commercial-use-allowed", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--derivative-use-allowed", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--annotator-id",
        default=None,
        help="the opaque annotator id the rows are recorded under (default: TCG_API_ANNOTATOR_ID)",
    )
    return parser


async def run(arguments: argparse.Namespace) -> dict[str, int]:
    """Read the manifests and originals, then rebuild the corpus they describe."""
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in arguments.manifest]
    photographs = [
        Photograph(
            data=row.path.read_bytes(),
            physical_copy_id=row.physical_copy_id,
            acquired_at=row.acquired_at,
        )
        for row in read_copies(arguments.copies)
    ]

    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    try:
        return await restore(
            engine,
            storage,
            manifests=manifests,
            photographs=photographs,
            license=arguments.license,
            commercial_use_allowed=arguments.commercial_use_allowed,
            derivative_use_allowed=arguments.derivative_use_allowed,
            annotator_id=arguments.annotator_id or settings.annotator_id,
        )
    finally:
        await engine.dispose()


def main() -> int:
    """Console-script entry point (`uv run tcg-restore-dataset-version`)."""
    arguments = _parser().parse_args()
    configure_logging(get_settings())

    try:
        counts = asyncio.run(run(arguments))
    except (RestoreRefused, ProvenanceRefused) as refusal:
        logger.error("corpus restore refused: %s", refusal)
        return 1

    logger.info(
        "corpus restored: %s. Next: normalize at the annotation-era detector, then "
        "tcg-publish-dataset-version --regenerate and git diff datasets/manifests/",
        ", ".join(f"{count} {name}" for name, count in counts.items()),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
