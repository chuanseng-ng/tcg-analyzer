"""Recording an uploaded photograph — issue #33, spec §11.

`tables.py` says what an image row *is*; this module is the only place one is
written or read. The split is `sessions.py`'s, which is `catalog/versions.py`'s:
DDL there, statements here. It shares `sessions.execute`, so there stays exactly
one place in the analysis domain where a driver failure becomes
:class:`~tcg_api.analysis.sessions.AnalysisStoreUnavailable`.

**A retake is a replacement, not a second photograph.** #31 put
`UNIQUE (analysis_id, side)` on the table precisely so nothing downstream has to
decide which of two fronts is the real one, so the write here is an upsert. The
update clears every column a later pipeline stage fills — `normalized_uri`,
`width`, `height`, `quality_score`, `quality_status` — because those describe
the photograph that has just been superseded, and a quality verdict about bytes
that no longer exist is worse than no verdict at all.

Nothing here writes `original_uri` from anything a client sent: the caller
generates the key. See `tcg_shared.storage.keys.generate_key`, which takes no
filename argument for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_domain.image_quality import QualityReport

from tcg_api.analysis.sessions import execute
from tcg_api.analysis.tables import images

__all__ = [
    "ImageQuality",
    "ImageRecord",
    "read_image_key",
    "read_quality",
    "read_v1_image_keys",
    "record_quality",
    "upsert_image",
    "v1_sides_present",
]

#: The columns a caller of this module gets back. Deliberately not the whole
#: row: the derived columns are NULL at upload time and a response that carried
#: them would invite a reader to render an empty value.
_IMAGE_COLUMNS: Final = (
    images.c.id,
    images.c.side,
    images.c.mime_type,
    images.c.sha256,
    images.c.created_at,
)


@dataclass(frozen=True, slots=True)
class ImageRecord:
    """One row of `images`, as far as the HTTP surface cares."""

    id: UUID
    side: str
    mime_type: str
    sha256: str
    created_at: datetime


def _record(row: sa.Row[Any]) -> ImageRecord:
    return ImageRecord(
        id=row.id,
        side=row.side,
        mime_type=row.mime_type,
        sha256=row.sha256,
        created_at=row.created_at,
    )


async def read_image_key(db: AsyncSession, analysis_id: UUID, side: ImageSide) -> str | None:
    """The storage key currently recorded for this side, or None if there is none.

    Read *before* an upsert so that a retake's superseded object can be deleted
    from storage afterwards. Cascading a row away does not delete an object, and
    an orphan is invisible to a retention sweep that works from rows (#41) — so
    an object nobody points at is one nobody will ever delete.
    """
    statement = sa.select(images.c.original_uri).where(
        images.c.analysis_id == analysis_id,
        images.c.side == side.value,
    )
    result = await execute(db, statement)
    return result.scalar_one_or_none()


async def upsert_image(
    db: AsyncSession,
    *,
    analysis_id: UUID,
    side: ImageSide,
    original_uri: str,
    mime_type: str,
    sha256: str,
) -> ImageRecord:
    """Record this photograph as the one for `side`, replacing any predecessor.

    Does not commit. The caller owns the transaction, so the row and the state
    transition it causes land together or not at all.
    """
    statement = postgres_insert(images).values(
        id=uuid4(),
        analysis_id=analysis_id,
        side=side.value,
        original_uri=original_uri,
        mime_type=mime_type,
        sha256=sha256,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_images_analysis_id_side",
        set_={
            "original_uri": statement.excluded.original_uri,
            "mime_type": statement.excluded.mime_type,
            "sha256": statement.excluded.sha256,
            # `created_at` is when *this* photograph arrived, not when the first
            # attempt at this side did.
            "created_at": sa.func.now(),
            # Everything a later stage computes describes the bytes that have
            # just been replaced. See the module docstring.
            "normalized_uri": None,
            "width": None,
            "height": None,
            "quality_score": None,
            "quality_status": None,
            "quality_details": None,
        },
    )
    result = await execute(db, statement.returning(*_IMAGE_COLUMNS))
    return _record(result.one())


async def v1_sides_present(db: AsyncSession, analysis_id: UUID) -> int:
    """How many of the sides V1 captures this analysis now has.

    Counted over :data:`tcg_domain.analysis.V1_SIDES` rather than over the whole
    table, because `images.side` admits all six of spec §11's values and an
    analysis is ready to run when its front and back have arrived — spec §52's
    guided-photography sides are not a precondition of anything in V1.
    """
    statement = sa.select(sa.func.count()).where(
        images.c.analysis_id == analysis_id,
        images.c.side.in_([side.value for side in V1_SIDES]),
    )
    result = await execute(db, statement)
    return int(result.scalar_one())


async def read_v1_image_keys(db: AsyncSession, analysis_id: UUID) -> dict[ImageSide, str]:
    """The storage keys of this analysis's front and back, in one round trip.

    Separate from :func:`read_image_key` rather than replacing it: the upload
    path wants exactly one side, and the quality gate wants both. Restricted to
    :data:`~tcg_domain.analysis.V1_SIDES` for the reason
    :func:`v1_sides_present` gives — the column admits all six of spec §11's
    values and V1 captures two.
    """
    statement = sa.select(images.c.side, images.c.original_uri).where(
        images.c.analysis_id == analysis_id,
        images.c.side.in_([side.value for side in V1_SIDES]),
    )
    result = await execute(db, statement)
    return {ImageSide(row.side): row.original_uri for row in result}


async def record_quality(
    db: AsyncSession,
    *,
    analysis_id: UUID,
    side: ImageSide,
    report: QualityReport,
) -> None:
    """Write the gate's verdict onto one image — spec §19, #36.

    The status is taken from the report rather than passed in, because
    :attr:`~tcg_domain.image_quality.QualityReport.status` is derived from the
    findings: a row saying `good` while its details carry a detected blur is not
    something a caller should be able to construct.

    Does not commit. The caller owns the transaction, so the verdict and the
    state transition it causes land together or not at all.
    """
    statement = (
        sa.update(images)
        .where(images.c.analysis_id == analysis_id, images.c.side == side.value)
        .values(
            quality_score=report.score,
            quality_status=report.status.value,
            quality_details=report.as_record(),
        )
    )
    await execute(db, statement)


@dataclass(frozen=True, slots=True)
class ImageQuality:
    """One image's verdict, as far as the HTTP surface cares.

    `details` is the raw JSONB. It is *not* rendered here: the router picks the
    fields a caller may see, and the measurements the gate recorded for M7 are
    not among them.
    """

    side: str
    quality_score: float | None
    quality_status: str | None
    details: dict[str, Any] | None


async def read_quality(db: AsyncSession, analysis_id: UUID) -> list[ImageQuality]:
    """Every image of this analysis and what the gate concluded about it.

    Ordered by `side` so a response is stable, and returned even when the gate
    has not run — an image with no verdict is a fact a poller needs, because it
    is the difference between "not assessed yet" and "assessed and clear".
    """
    statement = (
        sa.select(
            images.c.side,
            images.c.quality_score,
            images.c.quality_status,
            images.c.quality_details,
        )
        .where(images.c.analysis_id == analysis_id)
        .order_by(images.c.side)
    )
    result = await execute(db, statement)
    return [
        ImageQuality(
            side=row.side,
            quality_score=row.quality_score,
            quality_status=row.quality_status,
            details=row.quality_details,
        )
        for row in result
    ]
