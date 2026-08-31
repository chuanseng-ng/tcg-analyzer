"""Spec §31's `dataset_version`: freeze a corpus, and render what it left behind.

§31 requires every training run to reference an explicit, ordered dataset version
and forbids a model referencing `/latest/`. #156 decided the split and
deliberately persisted nothing; this module is the write. It creates the version,
writes every member's split **inside the same transaction**, and renders the
manifest from the rows that transaction wrote.

**One transaction is the whole immutability argument.** `dataset_versions` and
`dataset_members` both refuse an `UPDATE` in a trigger (#153), which stops a
frozen version being *edited*; nothing in the schema stops a member being
*appended* afterwards. What stops that is that there is no code path which does
it — `create_version` writes the members it was handed and this module exposes
nothing else that touches the table.

**The manifest is a render, never a record.** Counts, achieved proportions and
the provenance mix are recomputed from `dataset_members` every time, which is
what makes a regeneration byte-identical by construction rather than by care —
the same relationship `market_snapshots` has with its derived `data_version`
(#51), and the reason #153 stores none of the three as columns. `split_seed` is
the one thing on the row, because it is the one thing derivable from nothing.

**A manifest is publishable and the images are not.** ADR 0008 sets
`redistribution_allowed` to `false` on every approved source, including the
photographs this project took itself, so no dataset produced under it is ever
published; identifiers and content hashes carry no artwork and may be committed.
That asymmetry is the entire reason `datasets/manifests/` exists as a directory,
and ADR 0009 is why the file is what `ml/*` reads instead of the database.

**Nothing here imports OpenCV or any `tcg_ml_*` package.** It reaches the corpus
through :mod:`tcg_api.datasets.splitting`, which reaches stored hashes through
:mod:`tcg_api.datasets.fingerprints` — the pure half of #155.
`services/api/tests/test_import_purity.py` holds that.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_domain import VERSION_PATTERN
from tcg_domain.dataset import DatasetSplit

from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.splitting import SplitAssignment, split_corpus
from tcg_api.datasets.tables import (
    centering_measurements,
    dataset_members,
    dataset_versions,
    image_annotations,
    training_images,
)
from tcg_api.logging import configure_logging

__all__ = [
    "MANIFESTS_DIR",
    "DatasetVersion",
    "DatasetVersionRefused",
    "Manifest",
    "ManifestMember",
    "MemberAnnotation",
    "MemberCentering",
    "create_version",
    "main",
    "manifest_path",
    "read_manifest",
    "render_manifest",
    "run",
    "write_manifest",
]

logger = logging.getLogger(__name__)

#: Where a generated manifest goes. `parents[5]` walks out of
#: `services/api/src/tcg_api/datasets/` to the checkout root, which is
#: `catalog/seed.py`'s `DEFAULT_SEEDS_DIR` walk reused rather than reinvented. Not
#: a `Settings` field: it is a fact about the repository layout rather than about
#: a deployment, and a setting would also owe `.env.example` an entry.
MANIFESTS_DIR: Final = Path(__file__).resolve().parents[5] / "datasets" / "manifests"

#: The constraint a re-used version name trips, so `main` can say what it means
#: rather than reflecting the driver's message back at the operator.
_VERSION_UNIQUE: Final = "uq_dataset_versions_version"


class DatasetVersionRefused(ValueError):
    """The corpus cannot be frozen as asked.

    A `ValueError` because that is what refusing malformed input is, and the
    message names the rule rather than the constraint — the reader is the
    operator publishing the version.
    """


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """One frozen corpus — the `dataset_versions` row, as written.

    Args:
        id: The surrogate key `dataset_members` references.
        version: Spec §31's explicit, ordered identifier.
        ordinal: Publication order, assigned by the database.
        split_seed: The seed #156's splitter ran with.
        created_at: When it was frozen. Read back rather than chosen, and the
            reason a regenerated manifest is byte-identical.
    """

    id: uuid.UUID
    version: str
    ordinal: int
    split_seed: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemberAnnotation:
    """One `image_annotations` row, as the manifest carries it — #188.

    Everything a scorer needs and nothing else: `annotator_id` stays out of
    the committed file (§53's restraint, and there is one annotator), and so
    does `metadata`. `created_at` and `id` ride along because the tables are
    append-only — a correction is a new row, the newest row per
    `(kind, region)` is the current view, and the *reader* applies that rule;
    the manifest renders every row rather than choosing a collapse.
    """

    id: uuid.UUID
    kind: str
    region: str | None
    label: str
    severity: str | None
    confidence: float
    bbox: tuple[float, float, float, float] | None
    representation: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemberCentering:
    """One `centering_measurements` row, as the manifest carries it — #188.

    `notes` stays out of the committed file: it is free text. An unmeasured
    axis is `None` — §21's full-art and borderless layouts have no ratio on
    an axis, and inventing `0.5` is the confidently-wrong output §2.7 forbids.
    """

    id: uuid.UUID
    horizontal: float | None
    vertical: float | None
    confidence: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ManifestMember:
    """One image's line in a manifest.

    Identifiers, a content hash and a split assignment — never bytes. The
    provenance pair rides along so the class mix stays recomputable from the file
    itself, and `original_uri` so a training run can resolve the bytes without
    reading the database, which is what ADR 0009 requires of `ml/*`.

    Since #188 the annotation rows ride along too — #157's pre-authorized
    shape ("a field on `ManifestMember` and a regeneration, never a second
    file"), because `ml/evaluation` scores against them and `ml/*` reads a
    manifest, not the database. The rows render as they stand: annotating an
    image after its version was published changes the next render, so the
    byte-identity invariant is same-database-state → same-bytes, and a
    post-publication annotation earns a regenerated, re-committed file.
    """

    training_image_id: uuid.UUID
    sha256: str
    split: DatasetSplit
    side: str
    source: str
    acquisition_method: str
    original_uri: str
    annotations: tuple[MemberAnnotation, ...] = ()
    centering: tuple[MemberCentering, ...] = ()


@dataclass(frozen=True, slots=True)
class Manifest:
    """A dataset version, rendered from its rows.

    Everything a reader would be tempted to store is derived here instead:
    :attr:`counts`, :attr:`proportions` and :attr:`provenance` are counts over
    :attr:`members`, so the file and the database cannot disagree.
    """

    version: DatasetVersion
    members: tuple[ManifestMember, ...]

    @property
    def counts(self) -> dict[DatasetSplit, int]:
        """How many images each split received. Every split appears, zeros included."""
        tally = dict.fromkeys(DatasetSplit, 0)
        for member in self.members:
            tally[member.split] += 1
        return tally

    @property
    def proportions(self) -> dict[DatasetSplit, Fraction]:
        """The shares actually achieved — exact, and never the targets."""
        counts = self.counts
        total = sum(counts.values())
        if total == 0:
            return dict.fromkeys(DatasetSplit, Fraction(0))
        return {split: Fraction(count, total) for split, count in counts.items()}

    @property
    def provenance(self) -> dict[str, int]:
        """How many images came from each of ADR 0008's approved classes.

        Keyed on `source/acquisition_method`, which is the pair
        `tcg_api.datasets.ingestion.APPROVED_SOURCES` is keyed on — four entries
        over three `source` values, so `source` alone would merge the two classes
        that differ only in whether the card was already slabbed. A version that
        turns out to be 90% one contributor is something M7 needs to know before
        it reads a metric.
        """
        return dict(
            collections.Counter(
                f"{member.source}/{member.acquisition_method}" for member in self.members
            )
        )


async def create_version(
    connection: AsyncConnection,
    *,
    version: str,
    assignment: SplitAssignment,
) -> DatasetVersion:
    """Freeze one corpus: the version row, then every member's split.

    Args:
        connection: The caller's transaction. **Does not commit** — the version
            and its members land together or not at all, and that is the only
            thing that makes a member impossible to add afterwards.
        version: Spec §31's identifier, e.g. `pokemon-condition-v0.3.0`. The
            grammar is `version_is_an_explicit_identifier`'s to enforce; the
            command line explains it first.
        assignment: What #156's splitter decided. Its `seed` becomes
            `split_seed`; its `assignment` becomes the member rows.

    Raises:
        DatasetVersionRefused: If the assignment is empty. A version with no
            members is a reference a training run resolves to nothing, and §31's
            point is that the reference means something.
        IntegrityError: If the name is already published
            (`uq_dataset_versions_version`), or a member names an image that is
            not in the corpus.
    """
    if not assignment.assignment:
        raise DatasetVersionRefused(
            f"{version} would be frozen over an empty corpus. Ingest images with "
            f"tcg-ingest-training-images first — spec §31 requires a training run to "
            f"reference a version, and a version with no members references nothing."
        )

    row = (
        await connection.execute(
            sa.insert(dataset_versions).returning(
                dataset_versions.c.id,
                dataset_versions.c.ordinal,
                dataset_versions.c.created_at,
            ),
            {"id": uuid.uuid4(), "version": version, "split_seed": assignment.seed},
        )
    ).one()

    # One statement for the whole membership. Written here, in the transaction
    # that created the version above, rather than by any later caller.
    await connection.execute(
        sa.insert(dataset_members),
        [
            {
                "dataset_version_id": row.id,
                "training_image_id": training_image_id,
                "split": str(split),
            }
            for training_image_id, split in assignment.assignment.items()
        ],
    )

    return DatasetVersion(
        id=row.id,
        version=version,
        ordinal=row.ordinal,
        split_seed=assignment.seed,
        created_at=row.created_at,
    )


async def read_manifest(connection: AsyncConnection, *, version: str) -> Manifest:
    """Read one version and its members back out, ready to render.

    Called inside the transaction that created the version, and again by
    `--regenerate` years later. One function for both, because two would be two
    answers that drift.

    Raises:
        DatasetVersionRefused: If no version carries that name.
    """
    row = (
        await connection.execute(
            sa.select(
                dataset_versions.c.id,
                dataset_versions.c.ordinal,
                dataset_versions.c.split_seed,
                dataset_versions.c.created_at,
            ).where(dataset_versions.c.version == version)
        )
    ).one_or_none()
    if row is None:
        raise DatasetVersionRefused(f"no dataset version is published under the name {version!r}")

    members = await connection.execute(
        sa.select(
            dataset_members.c.training_image_id,
            dataset_members.c.split,
            training_images.c.sha256,
            training_images.c.side,
            training_images.c.source,
            training_images.c.acquisition_method,
            training_images.c.original_uri,
        )
        .select_from(
            dataset_members.join(
                training_images, dataset_members.c.training_image_id == training_images.c.id
            )
        )
        .where(dataset_members.c.dataset_version_id == row.id)
        # The render sorts too; ordering here as well keeps a query log readable
        # and costs nothing the index does not already give.
        .order_by(dataset_members.c.training_image_id)
    )

    member_of = sa.select(dataset_members.c.training_image_id).where(
        dataset_members.c.dataset_version_id == row.id
    )
    markers = await connection.execute(
        sa.select(
            image_annotations.c.training_image_id,
            image_annotations.c.id,
            image_annotations.c.kind,
            image_annotations.c.region,
            image_annotations.c.label,
            image_annotations.c.severity,
            image_annotations.c.confidence,
            image_annotations.c.bbox_x,
            image_annotations.c.bbox_y,
            image_annotations.c.bbox_width,
            image_annotations.c.bbox_height,
            image_annotations.c.representation,
            image_annotations.c.created_at,
        )
        .where(image_annotations.c.training_image_id.in_(member_of))
        .order_by(image_annotations.c.created_at, image_annotations.c.id)
    )
    annotations_by_image: dict[uuid.UUID, list[MemberAnnotation]] = collections.defaultdict(list)
    for marker in markers:
        annotations_by_image[marker.training_image_id].append(
            MemberAnnotation(
                id=marker.id,
                kind=marker.kind,
                region=marker.region,
                label=marker.label,
                severity=marker.severity,
                confidence=marker.confidence,
                bbox=(
                    None
                    if marker.bbox_x is None
                    else (marker.bbox_x, marker.bbox_y, marker.bbox_width, marker.bbox_height)
                ),
                representation=marker.representation,
                created_at=marker.created_at,
            )
        )

    measurements = await connection.execute(
        sa.select(
            centering_measurements.c.training_image_id,
            centering_measurements.c.id,
            centering_measurements.c.horizontal,
            centering_measurements.c.vertical,
            centering_measurements.c.confidence,
            centering_measurements.c.created_at,
        )
        .where(centering_measurements.c.training_image_id.in_(member_of))
        .order_by(centering_measurements.c.created_at, centering_measurements.c.id)
    )
    centering_by_image: dict[uuid.UUID, list[MemberCentering]] = collections.defaultdict(list)
    for measurement in measurements:
        centering_by_image[measurement.training_image_id].append(
            MemberCentering(
                id=measurement.id,
                horizontal=measurement.horizontal,
                vertical=measurement.vertical,
                confidence=measurement.confidence,
                created_at=measurement.created_at,
            )
        )

    return Manifest(
        version=DatasetVersion(
            id=row.id,
            version=version,
            ordinal=row.ordinal,
            split_seed=row.split_seed,
            created_at=row.created_at,
        ),
        members=tuple(
            ManifestMember(
                training_image_id=member.training_image_id,
                sha256=member.sha256,
                split=DatasetSplit(member.split),
                side=member.side,
                source=member.source,
                acquisition_method=member.acquisition_method,
                original_uri=member.original_uri,
                annotations=tuple(annotations_by_image.get(member.training_image_id, ())),
                centering=tuple(centering_by_image.get(member.training_image_id, ())),
            )
            for member in members
        ),
    )


def render_manifest(manifest: Manifest) -> str:
    """The manifest as text, identical for identical rows.

    Byte-identity is the acceptance criterion, so every source of drift is closed
    here rather than left to the caller: keys are sorted, members are ordered by
    identifier (a total order), proportions are exact `Fraction`s rendered as
    `'7/10'` rather than a rounded float, and the file ends in exactly one
    newline.

    **Nothing is stamped at render time.** No generated-at, no application
    version: `created_at` comes off the row and is stable, where either of those
    would make the first regeneration differ. The version identifier is already
    in the payload and in the filename.
    """
    payload = {
        "dataset_version": manifest.version.version,
        "id": str(manifest.version.id),
        "ordinal": manifest.version.ordinal,
        "created_at": manifest.version.created_at.isoformat(),
        "split_seed": manifest.version.split_seed,
        "counts": {str(split): count for split, count in manifest.counts.items()},
        "proportions": {str(split): str(share) for split, share in manifest.proportions.items()},
        "provenance": manifest.provenance,
        "members": [
            {
                "training_image_id": str(member.training_image_id),
                "sha256": member.sha256,
                "split": str(member.split),
                "side": member.side,
                "source": member.source,
                "acquisition_method": member.acquisition_method,
                "original_uri": member.original_uri,
                # Empty lists rather than absent keys, so a reader can tell "no
                # rows" from a file rendered before #188 added the fields.
                "annotations": [
                    _annotation_entry(marker)
                    for marker in sorted(
                        member.annotations, key=lambda marker: (marker.created_at, str(marker.id))
                    )
                ],
                "centering": [
                    _centering_entry(measurement)
                    for measurement in sorted(
                        member.centering,
                        key=lambda measurement: (measurement.created_at, str(measurement.id)),
                    )
                ],
            }
            for member in sorted(manifest.members, key=lambda member: str(member.training_image_id))
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _annotation_entry(marker: MemberAnnotation) -> dict[str, object]:
    """One marker as JSON — absent optionals are absent keys, `as_record()`'s rule."""
    entry: dict[str, object] = {
        "id": str(marker.id),
        "kind": marker.kind,
        "label": marker.label,
        "confidence": marker.confidence,
        "representation": marker.representation,
        "created_at": marker.created_at.isoformat(),
    }
    if marker.region is not None:
        entry["region"] = marker.region
    if marker.severity is not None:
        entry["severity"] = marker.severity
    if marker.bbox is not None:
        x, y, width, height = marker.bbox
        entry["bbox"] = {"x": x, "y": y, "width": width, "height": height}
    return entry


def _centering_entry(measurement: MemberCentering) -> dict[str, object]:
    """One measurement as JSON — an unmeasured axis is an absent key."""
    entry: dict[str, object] = {
        "id": str(measurement.id),
        "confidence": measurement.confidence,
        "created_at": measurement.created_at.isoformat(),
    }
    if measurement.horizontal is not None:
        entry["horizontal"] = measurement.horizontal
    if measurement.vertical is not None:
        entry["vertical"] = measurement.vertical
    return entry


def manifest_path(version: str, directory: Path = MANIFESTS_DIR) -> Path:
    """Where a version's manifest lives. The identifier is the filename."""
    return directory / f"{version}.json"


def write_manifest(manifest: Manifest, directory: Path = MANIFESTS_DIR) -> Path:
    """Render a manifest to disk and return where it went.

    `newline="\\n"` because the default translates to `os.linesep`, and a manifest
    written on Windows would otherwise differ byte for byte from the same version
    regenerated on Linux — which is exactly what "byte-identical on regeneration"
    forbids.
    """
    path = manifest_path(manifest.version.version, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest(manifest), encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# The command line — `uv run tcg-publish-dataset-version`
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--version",
        required=True,
        metavar="IDENTIFIER",
        help=(
            "spec §31's explicit, ordered identifier — e.g. pokemon-condition-v0.1.0. "
            "A moving pointer is refused: a training run that recorded one would name a "
            "different corpus every time it was read."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help=(
            "the seed spec §32's splitter runs with. Required unless --regenerate. "
            "Recorded on the version, because it is derivable from nothing and is the "
            "only thing that makes the split re-derivable years later."
        ),
    )
    parser.add_argument(
        "--manifests-dir",
        type=Path,
        default=MANIFESTS_DIR,
        metavar="DIRECTORY",
        help="where the generated manifest goes (default: %(default)s)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help=(
            "re-render an existing version's manifest and write nothing to the "
            "database. The manifest is a render rather than a record, so this is how a "
            "lost or truncated file is recovered — and it must reproduce the original "
            "byte for byte."
        ),
    )
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    # The message, not the guarantee: `version_is_an_explicit_identifier` on
    # `dataset_versions` is what makes '/latest/' unstorable. This says why first.
    if "latest" in arguments.version:
        parser.error(
            f"--version must name one immutable corpus, not a moving pointer: "
            f"{arguments.version!r}. Spec §31: a model must never simply reference "
            f"'/latest/'."
        )
    if not VERSION_PATTERN.match(arguments.version):
        parser.error(
            f"--version must look like 'pokemon-condition-v0.1.0', got {arguments.version!r}"
        )
    if arguments.regenerate:
        if arguments.seed is not None:
            parser.error(
                "--seed cannot be given with --regenerate: the seed is already on the "
                "version, and re-splitting under a new one would be a different dataset "
                "wearing the same name"
            )
    elif arguments.seed is None:
        parser.error("--seed is required when publishing a version")


async def run(arguments: argparse.Namespace) -> Manifest:
    """Freeze the corpus under the given name, or re-render an existing version."""
    engine = create_engine(get_settings())
    try:
        if arguments.regenerate:
            async with engine.connect() as connection:
                return await read_manifest(connection, version=arguments.version)

        async with engine.begin() as connection:
            # The corpus read and the member write share one transaction, so an
            # image ingested concurrently cannot land between them.
            # `split_corpus` already logs the group census and warns about an
            # empty split, so nothing is restated here.
            assignment = await split_corpus(connection, seed=arguments.seed)
            await create_version(connection, version=arguments.version, assignment=assignment)
            # Generated inside the transaction that created the version, from the
            # rows it just wrote — the derived-`data_version` rule (#51).
            return await read_manifest(connection, version=arguments.version)
    finally:
        await engine.dispose()


def _report(manifest: Manifest, path: Path, *, verb: str) -> None:
    """The summary the operator sees: what landed, where, and from whom."""
    counts = manifest.counts
    proportions = manifest.proportions
    logger.info(
        "dataset version %s %s at ordinal %d over %d image(s), seed %d",
        manifest.version.version,
        verb,
        manifest.version.ordinal,
        len(manifest.members),
        manifest.version.split_seed,
    )
    logger.info(
        "splits: %s",
        ", ".join(f"{split} {counts[split]} ({proportions[split]})" for split in DatasetSplit),
    )
    logger.info(
        "provenance: %s",
        ", ".join(f"{source} {count}" for source, count in sorted(manifest.provenance.items())),
    )
    logger.info("manifest written to %s", path)


def main() -> int:
    """Console-script entry point (`uv run tcg-publish-dataset-version`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        manifest = asyncio.run(run(arguments))
    except DatasetVersionRefused as refusal:
        logger.error("dataset version refused: %s", refusal)
        return 1
    except IntegrityError as conflict:
        if _VERSION_UNIQUE in str(conflict.orig):
            logger.error(
                "a dataset version is already published as %s; a version is immutable, "
                "so a re-split is a new version rather than an edit",
                arguments.version,
            )
        else:
            logger.error("dataset version refused by the database: %s", conflict.orig)
        return 1

    # ponytail: the file lands after the commit, so a write that fails leaves a
    # version with no manifest on disk. `--regenerate` is the recovery, and it is
    # sound because the manifest is a render of the rows rather than the record.
    _report(
        manifest,
        write_manifest(manifest, arguments.manifests_dir),
        verb="re-rendered" if arguments.regenerate else "published",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
