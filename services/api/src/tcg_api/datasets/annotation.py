"""What spec §30's annotation tool reads: a work list, one image, its bytes.

The reads behind `/internal/annotation`. `routers/annotation.py` holds HTTP and
nothing else, on `routers/cards.py`'s rule, and this is where the statements
live — which is also where #160's writes into `image_annotations` and
`centering_measurements` belong when it lands.

The writes are at the foot of the file, beside the reads and not in the
router — `routers/cards.py`'s rule. There is **no `UPDATE` anywhere in this
module**, deliberately: both tables refuse one, so a correction is a new row,
and `test_datasets_annotation_writes.py` reads this source and fails if one
appears.

**Nothing here produces an artifact.** It resolves `training_images.normalized_uri`
and fetches the object under it. Straightening a photograph needs OpenCV, which
`tests/test_import_purity.py` keeps out of every module `tcg_api.main` can
reach, so an artifact exists here only because
:mod:`tcg_api.datasets.normalization` produced it out of band — and an image
that has none is answered honestly rather than warped on the spot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.database import execute
from tcg_api.datasets.tables import (
    centering_measurements,
    image_annotations,
    training_images,
)

__all__ = [
    "ARTIFACT_MEDIA_TYPE",
    "BoundingBox",
    "CenteringReading",
    "DatasetStoreUnavailable",
    "DefectMarker",
    "ImageAnnotations",
    "Representation",
    "StoredImage",
    "StoredMarker",
    "StoredMeasurement",
    "TrainingImageDetail",
    "TrainingImageSummary",
    "WorkList",
    "read_annotations",
    "read_bytes",
    "read_image",
    "read_work_list",
    "record_annotations",
]

_UNREACHABLE: Final = "the training image corpus could not be read"
_UNWRITABLE: Final = "the annotation could not be written"

#: What a stored artifact is, spelled out rather than imported.
#: `tcg_ml_normalization.MEDIA_TYPE` is the source of truth and importing it
#: here would put OpenCV on the request path, which is the one thing
#: `tests/test_import_purity.py` will not allow — `datasets/tables.py` keeps
#: 756 and 1056 out of the schema for the same reason. If the artifact ever
#: stops being a PNG, this and that constant move together.
ARTIFACT_MEDIA_TYPE: Final = "image/png"


class DatasetStoreUnavailable(ConnectionError):
    """The dataset store could not be reached.

    The fifth per-domain name `tcg_api.database.execute` anticipates, beside
    `CatalogUnavailable`, `AnalysisStoreUnavailable`, `GradingRulesUnavailable`
    and `MarketSnapshotUnavailable`. Which store was unreachable is what a route
    turns into a `details.reason`, which is why this is not shared.
    """


#: Which of an image's two representations a caller wants. `normalized` is the
#: standardized artifact #158's coordinates are fractions of; `original` is the
#: photograph, which is all there is when no card could be located.
#:
#: A `str` rather than a `StrEnum` in `tcg_domain`: it names two objects this
#: schema stores, not a concept the domain reasons about, and `ImageSide` is
#: what the domain already owns here.
Representation = str

NORMALIZED: Final[Representation] = "normalized"
ORIGINAL: Final[Representation] = "original"


@dataclass(frozen=True, slots=True)
class TrainingImageSummary:
    """One row of the work list, and one entry in an image's `siblings`."""

    id: UUID
    side: str
    card_id: UUID | None
    physical_copy_id: UUID | None
    source: str
    created_at: datetime
    has_artifact: bool


@dataclass(frozen=True, slots=True)
class TrainingImageDetail:
    """One image, with the other photographs of the same physical copy.

    Args:
        siblings: **Empty when `physical_copy_id` is NULL**, which is an honest
            answer rather than a gap: ADR 0008's approved class 4 identifies no
            copy, and treating NULL as a group would make every consented upload
            a sibling of every other one.
        has_artifact: Whether a standardized artifact was stored. The same
            field every summary carries, deliberately: a detail *is* a summary
            with more on it, and a second field naming the same fact — a
            `representation` beside it — is how the two come to disagree. The
            client's rule is one line and lives in one place.
    """

    id: UUID
    side: str
    card_id: UUID | None
    physical_copy_id: UUID | None
    source: str
    created_at: datetime
    width: int
    height: int
    has_artifact: bool
    siblings: tuple[TrainingImageSummary, ...]


@dataclass(frozen=True, slots=True)
class StoredImage:
    """The bytes of one representation, and the type they were stored as."""

    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class WorkList:
    """One page of the images awaiting annotation, and how many there are."""

    images: tuple[TrainingImageSummary, ...]
    total: int


#: Awaiting annotation is the absence of **both** kinds of row. §30 splits its
#: eleven features across two tables (#158), so an image carrying only a
#: centering measurement has been worked on — a single anti-join against
#: `image_annotations` would put it back in the queue and invite a second,
#: contradictory reading.
#:
#: `NOT EXISTS` rather than two `LEFT JOIN … IS NULL`: both child tables carry an
#: index on `training_image_id`, which is exactly what an anti-join uses, and a
#: double outer join multiplies rows and then needs `DISTINCT` to undo itself.
_AWAITING_ANNOTATION: Final = sa.and_(
    sa.not_(sa.exists().where(image_annotations.c.training_image_id == training_images.c.id)),
    sa.not_(sa.exists().where(centering_measurements.c.training_image_id == training_images.c.id)),
)

_SUMMARY_COLUMNS: Final = (
    training_images.c.id,
    training_images.c.side,
    training_images.c.card_id,
    training_images.c.physical_copy_id,
    training_images.c.source,
    training_images.c.created_at,
    training_images.c.normalized_uri,
)


def _summary(row: sa.Row[tuple[object, ...]]) -> TrainingImageSummary:
    return TrainingImageSummary(
        id=row.id,
        side=row.side,
        card_id=row.card_id,
        physical_copy_id=row.physical_copy_id,
        source=row.source,
        created_at=row.created_at,
        # The key itself never leaves the service: it is server-generated and
        # internal (spec §55), and a badge only needs to know there is one.
        has_artifact=row.normalized_uri is not None,
    )


async def read_work_list(db: AsyncSession, *, limit: int, offset: int) -> WorkList:
    """Return one page of the images nobody has annotated yet.

    Ordered by `(created_at, id)` — a total order, so paging neither drops nor
    duplicates a row, which `GET /cards/search` established and for the same
    reason.

    ponytail: offset paging over a set that *shrinks* as annotations land, so a
    page boundary can skip a row once #160 writes. A keyset cursor on
    `(created_at, id)` is the fix, and it is worth building when an annotator
    trips over it rather than before — nothing writes an annotation today.
    """
    statement = (
        sa.select(*_SUMMARY_COLUMNS, sa.func.count().over().label("total"))
        .where(_AWAITING_ANNOTATION)
        .order_by(training_images.c.created_at, training_images.c.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).all()

    if not rows:
        # `count(*) OVER ()` travels on the rows, so an empty page carries no
        # total. Ask for it separately rather than reporting zero: an offset past
        # the end is an empty page of a non-empty queue.
        total_statement = (
            sa.select(sa.func.count()).select_from(training_images).where(_AWAITING_ANNOTATION)
        )
        total = (
            await execute(
                db, total_statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE
            )
        ).scalar_one()
        return WorkList(images=(), total=int(total))

    return WorkList(
        images=tuple(_summary(row) for row in rows),
        total=int(rows[0].total),
    )


async def read_image(db: AsyncSession, image_id: UUID) -> TrainingImageDetail | None:
    """Return one image and its siblings, or `None` if there is no such image."""
    statement = sa.select(
        *_SUMMARY_COLUMNS,
        training_images.c.width,
        training_images.c.height,
    ).where(training_images.c.id == image_id)
    row = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).one_or_none()
    if row is None:
        return None

    siblings: tuple[TrainingImageSummary, ...] = ()
    if row.physical_copy_id is not None:
        # A second statement rather than a self-join, because the NULL guard is
        # the thing that must not be got wrong here and a guard a reviewer can
        # see is worth a round trip. `ix_training_images_physical_copy_id` is
        # partial on exactly this predicate.
        sibling_statement = (
            sa.select(*_SUMMARY_COLUMNS)
            .where(
                training_images.c.physical_copy_id == row.physical_copy_id,
                training_images.c.id != image_id,
            )
            .order_by(training_images.c.side, training_images.c.id)
        )
        siblings = tuple(
            _summary(sibling)
            for sibling in (
                await execute(
                    db,
                    sibling_statement,
                    unavailable=DatasetStoreUnavailable,
                    message=_UNREACHABLE,
                )
            ).all()
        )

    return TrainingImageDetail(
        id=row.id,
        side=row.side,
        card_id=row.card_id,
        physical_copy_id=row.physical_copy_id,
        source=row.source,
        created_at=row.created_at,
        width=row.width,
        height=row.height,
        has_artifact=row.normalized_uri is not None,
        siblings=siblings,
    )


async def read_bytes(
    db: AsyncSession,
    storage: ObjectStorage,
    image_id: UUID,
    *,
    representation: Representation,
) -> StoredImage | None:
    """Return one representation's bytes, or `None` if it does not exist.

    `None` covers both "no such image" and "that image has no artifact", and
    deliberately: the detail endpoint has already told the caller which
    representation exists, so asking for the other one is asking for something
    that is not there. Substituting the photograph instead would hand #160 a
    frame whose coordinates mean nothing, silently.

    Raises:
        DatasetStoreUnavailable: If the row could not be read.
        StorageError: If the object store could not be reached.
        ObjectNotFound: If the row names bytes the store does not hold. That is
            a disagreement between two stores rather than a missing thing, and
            the route answers it differently from a `None`.
    """
    statement = sa.select(
        training_images.c.original_uri,
        training_images.c.normalized_uri,
        training_images.c.mime_type,
    ).where(training_images.c.id == image_id)
    row = (
        await execute(db, statement, unavailable=DatasetStoreUnavailable, message=_UNREACHABLE)
    ).one_or_none()
    if row is None:
        return None

    if representation == NORMALIZED:
        if row.normalized_uri is None:
            return None
        key, media_type = row.normalized_uri, ARTIFACT_MEDIA_TYPE
    else:
        key, media_type = row.original_uri, row.mime_type

    return StoredImage(data=await storage.get(StorageKey(key)), media_type=media_type)


# ---------------------------------------------------------------------------
# The writes — spec §30's controls, #160
# ---------------------------------------------------------------------------
#: Every column of a stored marker anything outside this module reads. The
#: bounding box travels as four of them because that is how the schema stores it
#: and how `num_nulls(...) IN (0, 4)` reads it; it becomes one object below,
#: where it is one thing to a caller.
_MARKER_COLUMNS: Final = (
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
    image_annotations.c.annotator_id,
    image_annotations.c.created_at,
)

_MEASUREMENT_COLUMNS: Final = (
    centering_measurements.c.id,
    centering_measurements.c.horizontal,
    centering_measurements.c.vertical,
    centering_measurements.c.confidence,
    centering_measurements.c.notes,
    centering_measurements.c.annotator_id,
    centering_measurements.c.created_at,
)


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Where a defect is, as fractions of the normalized artifact.

    Never pixels, and never a fraction of the photograph: `ml/normalization`
    warps every image to one artifact, so a coordinate in that space survives a
    retake and compares across cards. Four values here and four columns in the
    schema, but **one optional object** — `num_nulls(bbox_x, …) IN (0, 4)` is
    what makes a partial box unrepresentable, and a single object is that rule
    in Python.
    """

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class DefectMarker:
    """One corner, edge or surface marker to record — spec §14, §15, §16, §17."""

    kind: str
    region: str | None
    label: str
    severity: str | None
    confidence: float
    bbox: BoundingBox | None


@dataclass(frozen=True, slots=True)
class CenteringReading:
    """One centering measurement to record — spec §21, §13.

    Each ratio is optional on its own and at least one must be present: §21 names
    full-art and borderless layouts outright, so a card with no border on an axis
    has no ratio there, and inventing `0.5` for it is the confidently-wrong
    output spec §2.7 forbids.
    """

    horizontal: float | None
    vertical: float | None
    confidence: float
    notes: str | None


@dataclass(frozen=True, slots=True)
class StoredMarker:
    """One marker as it was stored, including what the service supplied."""

    id: UUID
    kind: str
    region: str | None
    label: str
    severity: str | None
    confidence: float
    bbox: BoundingBox | None
    annotator_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredMeasurement:
    """One centering measurement as it was stored."""

    id: UUID
    horizontal: float | None
    vertical: float | None
    confidence: float
    notes: str | None
    annotator_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImageAnnotations:
    """Everything recorded against one image, oldest first.

    **Not collapsed to a current reading per region.** Both tables are
    append-only, so a correction is a newer row and the newest row for a
    `(kind, region)` is the current view of it — but a surface has as many
    defects as it has, so no single collapsing rule is right for all three kinds.
    The rows travel as they are, and the tool decides what to show.
    """

    markers: tuple[StoredMarker, ...]
    centering: tuple[StoredMeasurement, ...]


def _box(row: sa.Row[tuple[object, ...]]) -> BoundingBox | None:
    if row.bbox_x is None:
        return None
    return BoundingBox(x=row.bbox_x, y=row.bbox_y, width=row.bbox_width, height=row.bbox_height)


def _marker(row: sa.Row[tuple[object, ...]]) -> StoredMarker:
    return StoredMarker(
        id=row.id,
        kind=row.kind,
        region=row.region,
        label=row.label,
        severity=row.severity,
        confidence=row.confidence,
        bbox=_box(row),
        annotator_id=row.annotator_id,
        created_at=row.created_at,
    )


def _measurement(row: sa.Row[tuple[object, ...]]) -> StoredMeasurement:
    return StoredMeasurement(
        id=row.id,
        horizontal=row.horizontal,
        vertical=row.vertical,
        confidence=row.confidence,
        notes=row.notes,
        annotator_id=row.annotator_id,
        created_at=row.created_at,
    )


async def read_annotations(db: AsyncSession, image_id: UUID) -> ImageAnnotations:
    """Return everything recorded against one image, oldest first.

    Composed beside `read_image` by the router rather than folded into it: the
    POST handler needs `read_image` for its own gate and has no use for these
    rows, and `ix_image_annotations_training_image_id` was declared for exactly
    this query.

    An image nobody has annotated answers with two empty tuples — the same fact
    the work list reports, and not an error.
    """
    marker_rows = (
        await execute(
            db,
            sa.select(*_MARKER_COLUMNS)
            .where(image_annotations.c.training_image_id == image_id)
            .order_by(image_annotations.c.created_at, image_annotations.c.id),
            unavailable=DatasetStoreUnavailable,
            message=_UNREACHABLE,
        )
    ).all()
    measurement_rows = (
        await execute(
            db,
            sa.select(*_MEASUREMENT_COLUMNS)
            .where(centering_measurements.c.training_image_id == image_id)
            .order_by(centering_measurements.c.created_at, centering_measurements.c.id),
            unavailable=DatasetStoreUnavailable,
            message=_UNREACHABLE,
        )
    ).all()

    return ImageAnnotations(
        markers=tuple(_marker(row) for row in marker_rows),
        centering=tuple(_measurement(row) for row in measurement_rows),
    )


async def record_annotations(
    db: AsyncSession,
    image_id: UUID,
    *,
    markers: Sequence[DefectMarker],
    centering: CenteringReading | None,
    annotator_id: str,
) -> ImageAnnotations:
    """Write one annotator's work on one image, and return what was stored.

    **One image, one call.** A marker belongs to the image whose artifact its
    coordinates are fractions of, and `training_images.side` is what says which
    face that is (#158 refuses a `side` column for the same reason). Accepting
    two images in one request would make it possible to file the back's corners
    against the front.

    Does not commit — the caller does, which is what makes every marker and the
    measurement one transaction. **There is no `UPDATE` path here and there will
    not be one**: `trg_image_annotations_immutable` refuses one, and a correction
    is a new row.

    The caller has already resolved the image; that read is what answers both the
    404 and the artifact gate, so this does not look for it again.

    ponytail: an image deleted between that read and this insert violates the
    foreign key and surfaces as a 500. The deletion is an operator honouring an
    ADR 0008 withdrawal, and `SELECT … FOR UPDATE` on every save to turn that
    into a 409 is a lock on the annotation path for a race nobody will run.

    Raises:
        DatasetStoreUnavailable: If the rows could not be written.
    """
    stored_markers: tuple[StoredMarker, ...] = ()
    if markers:
        insert_markers = (
            sa.insert(image_annotations)
            .values(
                [
                    {
                        "id": uuid4(),
                        "training_image_id": image_id,
                        "kind": marker.kind,
                        "region": marker.region,
                        "label": marker.label,
                        "severity": marker.severity,
                        "confidence": marker.confidence,
                        "bbox_x": None if marker.bbox is None else marker.bbox.x,
                        "bbox_y": None if marker.bbox is None else marker.bbox.y,
                        "bbox_width": None if marker.bbox is None else marker.bbox.width,
                        "bbox_height": None if marker.bbox is None else marker.bbox.height,
                        "annotator_id": annotator_id,
                    }
                    for marker in markers
                ]
            )
            .returning(*_MARKER_COLUMNS)
        )
        stored_markers = tuple(
            _marker(row)
            for row in (
                await execute(
                    db, insert_markers, unavailable=DatasetStoreUnavailable, message=_UNWRITABLE
                )
            ).all()
        )

    stored_centering: tuple[StoredMeasurement, ...] = ()
    if centering is not None:
        insert_centering = (
            sa.insert(centering_measurements)
            .values(
                id=uuid4(),
                training_image_id=image_id,
                horizontal=centering.horizontal,
                vertical=centering.vertical,
                confidence=centering.confidence,
                notes=centering.notes,
                annotator_id=annotator_id,
            )
            .returning(*_MEASUREMENT_COLUMNS)
        )
        stored_centering = (
            _measurement(
                (
                    await execute(
                        db,
                        insert_centering,
                        unavailable=DatasetStoreUnavailable,
                        message=_UNWRITABLE,
                    )
                ).one()
            ),
        )

    return ImageAnnotations(markers=stored_markers, centering=stored_centering)
