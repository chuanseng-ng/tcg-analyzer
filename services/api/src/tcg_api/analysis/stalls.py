"""Failing an analysis whose run was killed without saying so — issue #272.

Every other failure records itself: the run catches its exception, chooses a
reason from the type (#265) and writes it. One does not, and it is the one the
hard time limit produces. With `task_acks_late` and the prefork pool a hard
limit **kills the child**, Celery acks the message on the way out, and nothing
in `jobs.py` ever sees an exception — the run's transaction is rolled back by
the dying connection and the row is left exactly where it was.

**Where it was is `uploaded`, not `identifying`.** A run is a single
transaction: `_advance` claims the analysis, records spec §57, judges the
photographs, assesses the condition and predicts the grades, and commits once at
the end — `quality.py`, `condition.py` and `sessions.py` each say "does not
commit" for that reason. So `identifying` is never visible outside the run, and
a run that died leaves nothing behind at all: no reproducibility record, no
half-written verdicts, and a status of `uploaded`.

That is what this sweep has to work from, and the only clock it has is the
photographs'. `POST /analyses/{id}/images` writes `uploaded` as the second side
lands (`routers/analyses.py`), and `/analyze` runs the analysis in the same
click, so the newest `images.created_at` is when a run was asked for to within
a second. An analysis still `uploaded` long after that either was killed or was
never run at all, and both are stalled: nobody is coming back for it, and
`/results` has already stopped polling (#271, at the hard limit).

**A live run is not at risk**, and not because of the interval. The claim's
`UPDATE` holds the row lock for the whole run, so this sweep's own conditional
`UPDATE` blocks on it and then finds `identifying` rather than `uploaded` —
`state.transition` refuses, returns `False`, and the row is not counted. The
fifteen minutes are what keep the sweep from waiting on a lock at all.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus

from tcg_api.analysis.failures import FailureReason
from tcg_api.analysis.sessions import execute
from tcg_api.analysis.state import transition
from tcg_api.analysis.tables import analyses, images

__all__ = ["STALLED_AFTER_SECONDS", "sweep_stalled"]

logger = structlog.get_logger(__name__)

#: How long an analysis may sit in `uploaded` before it is given up on —
#: `docs/observability.md`, "What reads these numbers". The longest a legitimate
#: run can take from its claim is four attempts of the hard limit plus three
#: backoffs of at most a minute, eleven minutes; fifteen leaves room for the
#: queue wait ahead of it, which nobody has measured under real traffic. Changing
#: it changes that document in the same pull request.
STALLED_AFTER_SECONDS: Final = 15 * 60

#: The analyses now stalled, oldest photograph first. `uploaded` is the state a
#: killed run rolls back to and the state a run that was never enqueued stays in;
#: the newest photograph is the closest thing to "when a run was asked for" that
#: is written down. Compared against the database's clock, never this process's —
#: `retention.py`'s rule, and for its reason.
_STALLED = (
    sa.select(analyses.c.id, sa.func.max(images.c.created_at).label("photographed"))
    .select_from(analyses.join(images, images.c.analysis_id == analyses.c.id))
    .where(analyses.c.status == AnalysisStatus.UPLOADED.value)
    .group_by(analyses.c.id)
    .having(
        sa.func.max(images.c.created_at)
        < sa.func.now() - sa.text(f"interval '{STALLED_AFTER_SECONDS} seconds'")
    )
    .order_by(sa.func.max(images.c.created_at))
)


async def sweep_stalled(db: AsyncSession, *, limit: int) -> int:
    """Fail every analysis stalled in `uploaded`. Returns how many moved.

    The move goes through `state.transition` rather than an `UPDATE` of its own,
    because that is the only place the failure columns are written (#265) and
    the only place the legality of a move is decided. It is also the race guard:
    an analysis a worker claimed between the read and the write is no longer
    `uploaded`, so the statement matches nothing, returns `False`, and is not
    counted — the same conditional `UPDATE` that makes a duplicate delivery a
    no-op.

    Commits once. The rows are independent, but they are also all-or-nothing
    cheap: a sweep that fails halfway leaves them stalled for another hour,
    which is what the next tick is for.
    """
    result = await execute(db, _STALLED.limit(limit))
    stalled: list[UUID] = [row.id for row in result]

    swept = 0
    for analysis_id in stalled:
        if await transition(
            db, analysis_id, to=AnalysisStatus.FAILED, failure=FailureReason.STALLED
        ):
            swept += 1
    await db.commit()

    # Counts only, `retention.swept`'s rule: an identifier here would name whose
    # photographs went unread, and spec §54 keeps that off every line.
    logger.info("analysis.stalled_swept", count=swept)
    return swept
