"""Moving an analysis from one state to the next — issue #35, spec §65.

`tcg_domain.analysis` says which moves are legal. This module is the only place
one is performed, and it performs it as **one statement**:

    UPDATE analyses SET status = :to
     WHERE id = :id AND status IN (the states :to may be reached from)

That is not a stylistic preference. A read, a `can_transition` check and then an
`UPDATE` would be three steps with two races between them, and an at-least-once
queue guarantees both races happen: the same job is delivered twice, and both
deliveries read `uploaded` before either writes. Folding the check into the
`WHERE` clause makes PostgreSQL the arbiter, and makes one statement carry three
guarantees at once:

* an illegal move changes nothing, because no row matches;
* a duplicate delivery changes nothing, because the row has already moved;
* two concurrent claims cannot both win, because the second finds the first's
  value.

All three are the same fact — `rowcount == 1` means "this caller is the one that
moved it" — which is why :func:`transition` returns a bool rather than raising.
The caller decides what a refusal means: the HTTP layer answers 409, and the job
runner treats it as "somebody else already did this" and stops.

The split from `sessions.py` is deliberate but thin: that module owns creating
and reading, this one owns moving. They share `_execute`, so there stays exactly
one place where a driver failure becomes `AnalysisStoreUnavailable`.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import TERMINAL_STATUSES, AnalysisStatus, legal_predecessors

from tcg_api.analysis.failures import FailureReason
from tcg_api.analysis.sessions import execute
from tcg_api.analysis.tables import analyses

__all__ = ["transition"]


async def transition(
    db: AsyncSession,
    analysis_id: UUID,
    *,
    to: AnalysisStatus,
    failure: FailureReason | None = None,
) -> bool:
    """Move `analysis_id` to `to`, if that is legal from wherever it is now.

    Returns whether this call is the one that moved it. `False` covers every way
    it might not be — the analysis does not exist, it is somewhere `to` cannot be
    reached from, or another worker got there first — and the caller cannot tell
    those apart from here. That is deliberate: a job runner would do the same
    thing in all three cases, and the HTTP layer has already established
    ownership through `read_analysis` before it asks.

    Does not commit. The caller owns the transaction, so a transition and
    whatever it accompanies land together or not at all.

    A move to `failed` carries its `failure` — the reason, from which the §66
    code follows — in the **same statement** as the status, so a row can never
    be `failed` without its reason or carry a reason without being `failed`
    (#265); the table's CHECK says the same. Any other move refuses one.
    """
    if (failure is None) != (to is not AnalysisStatus.FAILED):
        raise ValueError("a move to `failed` needs a reason, and only that move takes one")
    sources = legal_predecessors(to)
    if not sources:
        # `created` is where a row starts, never somewhere it moves to. The
        # `IN ()` below would be valid SQL and match nothing, but saying so here
        # costs one line and avoids a round trip that cannot succeed.
        return False

    values: dict[str, object] = {"status": to.value}
    if to in TERMINAL_STATUSES:
        # `completed_at IS NULL OR status IN (terminal)` is a CHECK on the table,
        # so a terminal state without a timestamp is legal — and useless. The
        # database's clock, not this process's: an analysis is finished when the
        # row says so.
        values["completed_at"] = sa.func.now()
    if failure is not None:
        values["failure_code"] = failure.code.value
        values["failure_reason"] = failure.value

    statement = (
        sa.update(analyses)
        .where(
            analyses.c.id == analysis_id,
            analyses.c.status.in_(sorted(source.value for source in sources)),
        )
        .values(**values)
    )
    # `execute` is typed for the reads it was written for; an UPDATE always
    # produces a `CursorResult`, which is the only kind that counts rows. The
    # cast target is unquoted deliberately: quoted, `Any` appears nowhere CodeQL
    # can see it and the import reads as unused (py/unused-import).
    result = cast(sa.CursorResult[Any], await execute(db, statement))
    return result.rowcount == 1
