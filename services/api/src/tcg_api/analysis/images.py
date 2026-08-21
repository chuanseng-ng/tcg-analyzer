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

from tcg_api.analysis.sessions import execute
from tcg_api.analysis.tables import images

__all__ = ["ImageRecord", "read_image_key", "upsert_image", "v1_sides_present"]

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
