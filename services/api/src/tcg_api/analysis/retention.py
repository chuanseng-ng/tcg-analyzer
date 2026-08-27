"""Deleting what has expired — issue #41, spec §54 and §53.

Spec §54 says uploaded photographs may contain the user's hands, home and
personal surroundings, and that a retention policy must exist before production
launch. `analysis_sessions.expires_at` has been written on every session since
#31 and read by exactly one query — `sessions.resolve_session`, which merely
stops honouring the cookie. This module is what makes the date mean something.

**The whole policy is one period.** `TCG_API_SESSION_TTL_SECONDS`, seven days,
set once when the session opens. Originals, normalized artifacts, #39's cache
entries, analyses and the session row all go together, because they are one
cascade. There is no second horizon to keep in step and no exception to track.
`docs/retention.md` is the written version.

**One thing is not in that cascade and is swept anyway.** An analysis
*references* its economic configuration rather than owning it, so
`ON DELETE CASCADE` runs the other way and the configuration row would outlive
the session — holding what a user said they paid for their card, which is
theirs. The identifiers are read before the session goes and the rows deleted
after, which is the order the foreign key's RESTRICT requires; the
`economic_configurations` trigger guards `UPDATE` only so that this delete is
possible at all.

**The session row is deleted, not marked.** `analysis_sessions.
anonymous_session_id` *is* the browser's bearer token, so a row kept for
auditing is a per-browser identifier kept forever — which is what §53's "do not
permanently tie analyses to personal identity" and §54's "expire unless retained
for an explicitly justified purpose" both argue against. What is audited is the
`retention.swept` line: counts, and nothing that says whose photographs they
were. `SessionStatus.EXPIRED` and `SessionStatus.PURGED` are therefore values
nothing writes; see the note on the enum.

**Objects before rows, and that ordering is the whole correctness argument.**
The row is the only pointer to its objects. Delete it first and a storage
failure leaves an object nothing names — invisible to a sweep that works from
rows, which is spec §54's failure reached through §54's own mechanism. So each
session is swept in its own transaction: read the keys, delete the objects,
delete the row, commit. A `StorageError` rolls that session back and leaves it
due, and the objects already deleted are deleted again next time — `delete`
succeeds on an absent key by contract, which is what makes the whole sweep
re-runnable.

Per session rather than per batch for one reason: the batch is ordered by
`expires_at`, so aborting the batch on the first failure would re-pick the same
rows on every tick and one permanently unreadable key would stall retention
altogether.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import ObjectStorage, StorageError, StorageKey

from tcg_api.analysis.sessions import execute
from tcg_api.analysis.tables import analyses, analysis_sessions, images
from tcg_api.economics.tables import economic_configurations

__all__ = ["SWEEP_INTERVAL_SECONDS", "SWEEP_LIMIT", "Swept", "purge_expired"]

logger = structlog.get_logger(__name__)

#: How often the sweep runs, in seconds. Hourly: the retention period is seven
#: days, so the granularity that matters is "well inside a day".
SWEEP_INTERVAL_SECONDS: Final = 3600

#: How many sessions one sweep will take. Deliberately a constant rather than a
#: setting — the period is the policy and it already has an environment
#: variable; this is a batch size nobody reviews.
#:
#: ponytail: 200 an hour is the ceiling. Raise it, or shorten the interval, if a
#: backlog ever outruns it; there is no reason to make either configurable
#: before that happens.
SWEEP_LIMIT: Final = 200


@dataclass(frozen=True, slots=True)
class Swept:
    """What one sweep did. The numbers behind the `retention.swept` line."""

    sessions: int
    objects: int
    failed: int


#: The sessions now due, oldest first — the query `ix_analysis_sessions_expires_at`
#: was declared for, and the one `test_analysis_schema.py` already pins. Compared
#: against the database's clock, never this process's, so a skewed application
#: host cannot extend or shorten anyone's retention.
_DUE = (
    sa.select(analysis_sessions.c.id)
    .where(analysis_sessions.c.expires_at < sa.func.now())
    .order_by(analysis_sessions.c.expires_at)
)


async def purge_expired(db: AsyncSession, storage: ObjectStorage, *, limit: int) -> Swept:
    """Delete the objects and then the rows of every session past its expiry.

    Commits per session, so a partial sweep is a completed sweep of fewer
    sessions rather than a rollback of all of them. Safe to run concurrently
    with itself and safe to re-run: a session already gone is not due, and an
    object already deleted deletes again without complaint.
    """
    result = await execute(db, _DUE.limit(limit))
    due = [row.id for row in result]

    swept = 0
    removed = 0
    failed = 0
    for session_id in due:
        try:
            removed += await _sweep_one(db, storage, session_id)
        except StorageError as error:
            # The row stays due. Nothing has been deleted from the database for
            # this session, so its objects are still reachable next tick — which
            # is the entire point of deleting them first.
            await db.rollback()
            failed += 1
            logger.warning(
                "retention.session_not_swept",
                session_id=str(session_id),
                error=type(error).__name__,
            )
            continue
        swept += 1

    # Counts only. A key here would name the photograph that was deleted, and a
    # log nobody expires is not an improvement on a bucket nobody expires.
    logger.info("retention.swept", sessions=swept, objects=removed, failed=failed)
    return Swept(sessions=swept, objects=removed, failed=failed)


async def _sweep_one(db: AsyncSession, storage: ObjectStorage, session_id: UUID) -> int:
    """Delete one session's objects, then the session. Returns objects removed.

    The `DELETE` is one statement for the whole tree: `ON DELETE CASCADE` runs
    session → analysis → image, so the analyses, the image rows and with them
    #39's cache entries all go with it.
    """
    # Both columns: the original the user uploaded and the normalized artifact
    # derived from it, which is just as much a picture of their living room.
    objects = (
        sa.select(images.c.original_uri, images.c.normalized_uri)
        .select_from(images.join(analyses, images.c.analysis_id == analyses.c.id))
        .where(analyses.c.session_id == session_id)
    )
    result = await execute(db, objects)
    keys = [uri for row in result for uri in (row.original_uri, row.normalized_uri) if uri]

    # Read before the cascade, because after it there is no row left to read
    # them from — an orphaned configuration would be unreachable rather than
    # merely undeleted.
    referenced = await execute(
        db,
        sa.select(analyses.c.economic_configuration_id).where(
            analyses.c.session_id == session_id,
            analyses.c.economic_configuration_id.is_not(None),
        ),
    )
    configurations = [row.economic_configuration_id for row in referenced]

    for key in keys:
        await storage.delete(StorageKey(key))

    await execute(db, sa.delete(analysis_sessions).where(analysis_sessions.c.id == session_id))
    if configurations:
        # After the cascade: the foreign key is RESTRICT, so a configuration an
        # analysis still names cannot go first.
        await execute(
            db,
            sa.delete(economic_configurations).where(
                economic_configurations.c.id.in_(configurations)
            ),
        )
    await db.commit()
    return len(keys)
