"""Deleting objects that no row names — issue #264, spec §54.

`retention.py` sweeps from rows: it reads a session's keys, deletes those
objects and then the rows. That is the right order, and it is also its blind
spot. **The row is the only pointer to its objects**, so an object whose row was
never committed is invisible to it permanently — spec §54's failure reached
through §54's own mechanism, and the one thing `docs/retention.md` named as
uncovered that grows under real users.

Three paths leak one today, each documented where it occurs: a run killed
between `quality.py`'s `put` and `_advance`'s single commit (bounded at three
per side by the retry limit, since every attempt mints a fresh key), the same
leak reached through `_copy_artifact`, and a `_discard` that could not delete
the photograph a retake replaced (`routers/analyses.py`, `image.orphaned`).

**The key carries its own age.** `generate_key` mints
`namespace/YYYY/MM/DD/uuid4` precisely so that this sweep can ask the store for
one expired day at a time (ADR 0002). So there is no listing of the bucket and
no object metadata to read: the day prefix *is* the age, and
:func:`~tcg_shared.storage.keys.day_prefix` is the only thing that builds one.

**Nothing a row names is deleted, ever.** The set is "keys under an expired day
prefix, minus every key an `images` row still names", and the subtraction is a
single query against the live table. That is what makes the sweep safe while
retention is behind: a backlogged session's photographs are still named, so they
go when their session does and not before. A row committed between the listing
and the delete names an object minted *today*, which is not under a prefix this
sweep walks.

**Only `uploads/` and `normalized/`.** The corpus namespaces — `training/` and
`training-normalized/` — are outside the walk on purpose: retaining a training
image is a separately justified purpose under ADR 0008 and M6's provenance
rules, and no `images` row names one, so a sweep that walked them would delete
the corpus on its first tick.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import ObjectStorage, StorageKey, day_prefix

from tcg_api.analysis.images import NORMALIZED_NAMESPACE, UPLOAD_NAMESPACE
from tcg_api.analysis.sessions import execute
from tcg_api.analysis.tables import images

__all__ = [
    "LOOKBACK_DAYS",
    "ORPHAN_MARGIN_SECONDS",
    "SWEPT_NAMESPACES",
    "sweep_orphans",
]

logger = structlog.get_logger(__name__)

#: How far past the retention period a day must be before its objects are
#: considered abandoned. One day, because a key's date is the day it was
#: *minted* while retention counts from when the session was *opened*: a session
#: opened at 23:59 names objects under that day and expires almost a day later.
#: It also clears the run's own retry window, which is minutes.
ORPHAN_MARGIN_SECONDS: Final = 24 * 3600

#: How many day prefixes one run walks, ending at the cutoff. The hourly beat
#: means a day is swept twenty-four times before the window moves past it, so
#: one would very nearly do; seven is what makes a week of worker downtime
#: recoverable rather than permanent. A day older than this is never reached
#: again — the accepted bound, and `test_analysis_orphans.py` asserts it rather
#: than leaving it to be discovered.
LOOKBACK_DAYS: Final = 7

#: The namespaces a user's photographs and their artifacts live under, and the
#: only two this sweep walks. Both come from `images.py` rather than from their
#: writers: `quality.py` binds OpenCV and `routers/analyses.py` imports `jobs.py`,
#: which is the module that schedules this.
SWEPT_NAMESPACES: Final = (UPLOAD_NAMESPACE, NORMALIZED_NAMESPACE)


async def sweep_orphans(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    ttl_seconds: int,
    limit: int,
) -> int:
    """Delete every expired object no `images` row names. Returns how many went.

    Reads nothing but `images` and writes nothing at all, so there is no
    transaction to commit and no row to leave in a half-swept state. Safe to
    re-run and safe to run concurrently with itself: deleting an object that is
    already gone succeeds by contract, which is what `retention.py` relies on
    too.

    A `StorageError` propagates. The task has no retries and the next tick is an
    hour away, which is a gentler retry than any backoff — and unlike the
    row-driven sweep there is nothing left half-done to reason about.
    """
    now = await _now(db)
    cutoff = (now - timedelta(seconds=ttl_seconds + ORPHAN_MARGIN_SECONDS)).date()

    candidates = await _candidates(storage, cutoff, limit=limit)
    named = await _named_keys(db, candidates)

    removed = 0
    for key in candidates:
        if str(key) in named:
            continue
        await storage.delete(key)
        removed += 1

    # Counts only, `retention.swept`'s rule: a key here would name the
    # photograph that was deleted, and a log nobody expires is not an
    # improvement on a bucket nobody expires.
    logger.info("retention.orphans_swept", candidates=len(candidates), objects=removed)
    return removed


async def _now(db: AsyncSession) -> datetime:
    """The database's clock, `retention.py`'s rule and for its reason.

    A skewed application host must not be able to decide that a day still being
    written to has expired.
    """
    result = await execute(db, sa.select(sa.func.now()))
    at: datetime = result.scalar_one()
    return at


async def _candidates(storage: ObjectStorage, cutoff: date, *, limit: int) -> list[StorageKey]:
    """Every key under an expired day prefix, up to `limit`.

    Walked newest first so that a run capped by `limit` makes progress on the
    days most likely to hold something, and so that repeated runs converge
    rather than re-reading the same empty week.
    """
    found: list[StorageKey] = []
    for offset in range(LOOKBACK_DAYS):
        day = cutoff - timedelta(days=offset)
        for namespace in SWEPT_NAMESPACES:
            found.extend(await storage.list(day_prefix(namespace, day)))
            if len(found) >= limit:
                return found[:limit]
    return found


async def _named_keys(db: AsyncSession, candidates: list[StorageKey]) -> set[str]:
    """Which of `candidates` an `images` row still points at.

    Both columns, because a normalized artifact is as much a picture of somebody's
    living room as the photograph it was derived from — `retention.py` reads the
    pair for the same reason.
    """
    if not candidates:
        return set()

    keys = [str(key) for key in candidates]
    result = await execute(
        db,
        sa.select(images.c.original_uri, images.c.normalized_uri).where(
            sa.or_(images.c.original_uri.in_(keys), images.c.normalized_uri.in_(keys))
        ),
    )
    return {uri for row in result for uri in (row.original_uri, row.normalized_uri) if uri}
