"""Starting and reading an anonymous analysis — spec §53's write side.

`tables.py` says what a session and an analysis *are*; this module is the only
place that creates or reads one. The split is `catalog/versions.py`'s: DDL
there, statements here.

**There is no port here, and that is deliberate.**
:class:`tcg_domain.repository.CardRepository` exists because the catalog source
is a replaceable external provider (ADR 0004) and because the domain owns the
query and page types that cross it. Neither is true of an analysis session:
nothing is behind it but this service's own PostgreSQL, and the domain has no
analysis entity to return. An interface with one implementation and no second
candidate is ceremony. What the port idiom is actually load-bearing for is kept:
no driver exception escapes this module, because a caller should not have to
know which database library the service happens to use.

**The session id is a bearer token.** V1 has no accounts, so possession of the
token is the whole of the authorisation story — which is why
:func:`new_session_token` uses `secrets` rather than `uuid4`, and why
:func:`read_analysis` puts the session in the `WHERE` clause. A check applied
after the row has been fetched is a check somebody eventually forgets to apply;
a join condition is not.

Nothing here records an IP address, a user agent, or anything else about a
person. Spec §53 says not to tie an analysis to personal identity, and the
cheapest way to keep that promise is to never collect the data.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import SessionStatus

from tcg_api.analysis.tables import analyses, analysis_sessions

__all__ = [
    "TOKEN_BYTES",
    "AnalysisRecord",
    "AnalysisStoreUnavailable",
    "create_analysis",
    "create_session",
    "new_session_token",
    "read_analysis",
    "resolve_session",
]

#: How much entropy a session token carries. 256 bits, rendered by
#: `token_urlsafe` as 43 URL-safe characters. Generous on purpose: this is the
#: only secret an anonymous user has, it is never rotated within a session, and
#: the cost of another 16 bytes is nothing at all.
TOKEN_BYTES: Final = 32


class AnalysisStoreUnavailable(ConnectionError):
    """The analysis store could not be reached.

    Not invalid input — the request was well-formed and the answer is simply not
    obtainable right now. The local counterpart of
    :class:`tcg_domain.errors.CatalogUnavailable`, and an ordinary
    `ConnectionError` for the same reason: a caller catching it does not have to
    know this module's private hierarchy.

    It lives here rather than in `tcg_domain.errors` because there is no domain
    port for it to be part of the contract of. If an analysis ever grows a
    domain entity and a port, this is the exception that moves.
    """


@dataclass(frozen=True, slots=True)
class AnalysisRecord:
    """One row of `analyses`, as far as the HTTP surface cares.

    A frozen dataclass rather than a `sa.Row`, so the router is not indexing
    into an untyped tuple and so what this module promises to return is written
    down. Not a domain entity: an analysis has no behaviour yet, and inventing
    one before #35's state machine exists would be guessing at its shape.
    """

    id: UUID
    status: str
    created_at: datetime
    completed_at: datetime | None
    card_id: UUID | None


def new_session_token() -> str:
    """Mint an unguessable anonymous session identifier — spec §53.

    `secrets`, never `random` and never a sequence: a token an attacker can
    predict is every session's photographs. It is derived from nothing — not
    from a clock, not from a request, and certainly not from anything about a
    person — so it carries no information beyond being hard to guess.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


_ANALYSIS_COLUMNS: Final = (
    analyses.c.id,
    analyses.c.status,
    analyses.c.created_at,
    analyses.c.completed_at,
    analyses.c.card_id,
)


def _record(row: sa.Row[Any]) -> AnalysisRecord:
    return AnalysisRecord(
        id=row.id,
        status=row.status,
        created_at=row.created_at,
        completed_at=row.completed_at,
        card_id=row.card_id,
    )


async def _execute(db: AsyncSession, statement: Any) -> sa.Result[Any]:
    """Run `statement`, translating every driver failure into one exception.

    `OSError` alongside `SQLAlchemyError` for the reason `catalog/versions.py`
    gives: a refused connection never becomes a SQLAlchemy error at all, since
    asyncpg opens its socket through asyncio and raises `ConnectionRefusedError`
    before the dialect has anything to wrap. That is precisely the case this
    exception exists to name, so it must not be the one that escapes.
    """
    try:
        return await db.execute(statement)
    except (SQLAlchemyError, OSError) as error:
        raise AnalysisStoreUnavailable("The analysis store could not be reached.") from error


async def resolve_session(db: AsyncSession, token: str | None) -> UUID | None:
    """The live session this token names, or None if there is none.

    Absent, unknown, expired and purged are one answer on purpose. A caller that
    could tell them apart would learn whether a token it guessed ever existed,
    and there is nothing useful it could do differently anyway: every one of
    them means "you are not in a session", and the response to that is to start
    one rather than to explain.

    Expiry is compared in the database (`now()`), not against a timestamp this
    process computed, so a skewed application clock cannot extend a session.
    """
    if not token:
        return None

    statement = sa.select(analysis_sessions.c.id).where(
        analysis_sessions.c.anonymous_session_id == token,
        analysis_sessions.c.status == SessionStatus.ACTIVE.value,
        analysis_sessions.c.expires_at > sa.func.now(),
    )
    result = await _execute(db, statement)
    return result.scalar_one_or_none()


async def create_session(
    db: AsyncSession,
    *,
    token: str,
    ttl_seconds: int,
    application_version: str,
) -> tuple[UUID, datetime]:
    """Open a session for `token`, returning its internal id and expiry.

    `expires_at` is computed here rather than defaulted by the column, because
    #31 left it `NOT NULL` with no server default precisely so the retention
    period would be visible at a call site a reviewer reads. It is the caller's
    `ttl_seconds`, not this module's opinion.

    The token is the caller's rather than this function's, so that whoever sets
    the cookie and whoever writes the row are looking at the same string.
    """
    session_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    statement = sa.insert(analysis_sessions).values(
        id=session_id,
        anonymous_session_id=token,
        expires_at=expires_at,
        application_version=application_version,
    )
    await _execute(db, statement)
    return session_id, expires_at


async def create_analysis(db: AsyncSession, session_id: UUID) -> AnalysisRecord:
    """Start one analysis in `session_id`.

    `status` and `created_at` are left to their column defaults and read back
    with `RETURNING`, so the row this answers with is the row PostgreSQL wrote —
    `created` is the schema's first state, not a second copy of it here.
    `card_id` stays NULL until the user confirms an identification (spec §20).
    """
    statement = (
        sa.insert(analyses).values(id=uuid4(), session_id=session_id).returning(*_ANALYSIS_COLUMNS)
    )
    result = await _execute(db, statement)
    return _record(result.one())


async def read_analysis(
    db: AsyncSession,
    analysis_id: UUID,
    session_id: UUID,
) -> AnalysisRecord | None:
    """One analysis, but only if `session_id` is the session that started it.

    The ownership test is a `WHERE` clause rather than a comparison after the
    fetch, which is what makes "another session's analysis is not readable" a
    property of the query instead of a step a later caller can omit. The two
    misses it collapses — no such analysis, and not yours — are indistinguishable
    to a caller, which is the point.
    """
    statement = sa.select(*_ANALYSIS_COLUMNS).where(
        analyses.c.id == analysis_id,
        analyses.c.session_id == session_id,
    )
    result = await _execute(db, statement)
    row = result.one_or_none()
    return None if row is None else _record(row)
