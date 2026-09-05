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
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import SessionStatus

from tcg_api.analysis.tables import analyses, analysis_sessions

# Aliased: this module exports its own `execute`, which would otherwise shadow it.
from tcg_api.database import execute as _execute

__all__ = [
    "TOKEN_BYTES",
    "AnalysisRecord",
    "AnalysisStoreUnavailable",
    "create_analysis",
    "create_session",
    "execute",
    "new_session_token",
    "read_analysis",
    "read_condition",
    "read_grade_predictions",
    "record_condition",
    "record_grade_predictions",
    "record_reproducibility",
    "resolve_session",
    "set_confirmed_card",
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
    #: Spec §57's reproducibility record, as stored. Read from the row rather
    #: than resolved here: the record is what the run captured, and a value the
    #: HTTP layer worked out at read time would be a description of *now*.
    application_version: str | None
    model_bundle_version: str | None
    card_database_version: str | None
    grading_rules_version: str | None
    market_snapshot_id: UUID | None
    economic_configuration_id: UUID | None


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
    analyses.c.application_version,
    analyses.c.model_bundle_version,
    analyses.c.card_database_version,
    analyses.c.grading_rules_version,
    analyses.c.market_snapshot_id,
    analyses.c.economic_configuration_id,
)


def _record(row: sa.Row[Any]) -> AnalysisRecord:
    return AnalysisRecord(
        id=row.id,
        status=row.status,
        created_at=row.created_at,
        completed_at=row.completed_at,
        card_id=row.card_id,
        application_version=row.application_version,
        model_bundle_version=row.model_bundle_version,
        card_database_version=row.card_database_version,
        grading_rules_version=row.grading_rules_version,
        market_snapshot_id=row.market_snapshot_id,
        economic_configuration_id=row.economic_configuration_id,
    )


async def execute(db: AsyncSession, statement: Any) -> sa.Result[Any]:
    """Run `statement`, translating every driver failure into one exception.

    Public because `state.py`, `images.py` and `retention.py` all perform their
    statements through it: there is one analysis store, so there should be one
    place where its driver's failures stop being the driver's. Kept as this
    module's own name after the translation itself was hoisted into
    `database.execute` (#56), so those three import one exception's worth of
    context rather than repeating it at each call.
    """
    return await _execute(
        db,
        statement,
        unavailable=AnalysisStoreUnavailable,
        message="The analysis store could not be reached.",
    )


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
    result = await execute(db, statement)
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
    await execute(db, statement)
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
    result = await execute(db, statement)
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
    result = await execute(db, statement)
    row = result.one_or_none()
    return None if row is None else _record(row)


async def set_confirmed_card(
    db: AsyncSession,
    analysis_id: UUID,
    card_id: UUID,
) -> AnalysisRecord:
    """Record the card the user confirmed against `analysis_id` — spec §20, #104.

    `RETURNING` rather than a second read, on `create_analysis`'s precedent: the
    record this answers with is the row PostgreSQL wrote, including the status
    the transition alongside it has already set, rather than a copy assembled
    here from what the caller believes it did.

    Does not commit, and does not check anything. The caller has established
    ownership through :func:`read_analysis` and legality through
    `state.transition`, whose conditional `UPDATE` is the arbiter; this is the
    write that accompanies it inside the same transaction. `card_id` is a
    catalog identifier the router has already resolved — the `RESTRICT` foreign
    key is the backstop, not the check.
    """
    statement = (
        sa.update(analyses)
        .where(analyses.c.id == analysis_id)
        .values(card_id=card_id)
        .returning(*_ANALYSIS_COLUMNS)
    )
    result = await execute(db, statement)
    return _record(result.one())


async def record_reproducibility(
    db: AsyncSession,
    analysis_id: UUID,
    *,
    application_version: str,
    card_database_version: str | None,
    market_snapshot_id: UUID | None,
    model_bundle_version: str,
    grading_rules_version: str | None,
) -> None:
    """Write spec §57's reproducibility record onto `analysis_id`.

    Called once, by the run that claimed the analysis, inside the claim's
    transaction. That placement is the whole point of the record: §57's values
    must be the ones that were in force when the analysis executed, and a
    resolution done later — at read time, or by a second job — would describe
    whichever versions happen to be current then.

    Takes plain values rather than a :class:`CardDatabaseVersion`, on
    `images.record_normalization`'s precedent: the caller has already resolved
    them, and a signature naming a catalog type would make this module import
    the catalog to write two strings.

    `market_snapshot_id` is `None` until something has ingested: ADR 0006 gates
    commercial use on a subscription that is not yet active, so no provider is
    registered and no snapshot exists to point at. It is still a **required**
    argument rather than one defaulting to `None` — a default is how a caller
    that ought to record a snapshot silently stops.

    `model_bundle_version` is the composed condition version (#187) joined to
    the composed grading version (#227, ADR 0011 decision 6) — compile-time
    constants of the ml packages, so it is resolvable at the claim like every
    other field, and recorded whether or not the run later reaches either
    step: the record says which versions were in force, not which stages
    completed.

    `grading_rules_version` is the same idea for the published standards: one
    string naming all three companies' versions in force at the claim, joined
    with `+` in slug order, because no company has been selected yet and all
    three were in force (#227, ADR 0011). What was in force, not what was
    consulted — a V1 predictor reads no machine-readable rules. `None` when
    some company had no standard recorded, because a partial composite would
    misreport; and like `market_snapshot_id` it is **required** rather than
    defaulted, for the same reason.

    Does not commit; the caller owns the transaction. Writing twice is refused
    by `trg_analyses_reproducibility_immutable` rather than by a check here,
    which is what makes the record immutable against every writer rather than
    against this one.
    """
    statement = (
        sa.update(analyses)
        .where(analyses.c.id == analysis_id)
        .values(
            application_version=application_version,
            card_database_version=card_database_version,
            market_snapshot_id=market_snapshot_id,
            model_bundle_version=model_bundle_version,
            grading_rules_version=grading_rules_version,
        )
    )
    await execute(db, statement)


async def record_condition(
    db: AsyncSession,
    analysis_id: UUID,
    *,
    details: dict[str, object],
) -> None:
    """Write the condition step's document onto `analysis_id` — #187.

    `details` is either the assessment's record or a top-level
    `insufficient_information` with its reason, each beside the composed
    version and the analyzers' thresholds — the step always writes one, so a
    NULL keeps meaning "the step never ran". A plain document rather than a
    domain type, on :func:`record_reproducibility`'s reasoning: the caller has
    already rendered it, and a signature naming `ConditionAssessment` would be
    harmless here but would invite rendering in two places.

    Does not commit; the caller owns the transaction, so the document lands
    with the transition it precedes or not at all. Deliberately no trigger and
    no second-write guard: the single writer inside the claim is the
    guarantee, and the document is not §57's record.
    """
    statement = (
        sa.update(analyses).where(analyses.c.id == analysis_id).values(condition_details=details)
    )
    await execute(db, statement)


async def read_condition(db: AsyncSession, analysis_id: UUID) -> dict[str, object] | None:
    """The condition step's document for `analysis_id`, or `None` if it never ran — #227.

    The whole document, as stored: the grading step reads it (never the
    analyzers) and rehydrates the assessment itself, and since #245 the results
    route does the same to put it on the wire. No session in the `WHERE`
    clause: ownership is the caller's — the worker inside its claim, or a route
    for which `read_analysis` has already established it.
    """
    statement = sa.select(analyses.c.condition_details).where(analyses.c.id == analysis_id)
    result = await execute(db, statement)
    document = result.scalar_one_or_none()
    return None if document is None else dict(document)


async def read_grade_predictions(db: AsyncSession, analysis_id: UUID) -> dict[str, object] | None:
    """The grade prediction step's document for `analysis_id`, or `None` if it never ran — #228.

    :func:`read_condition`'s twin one stage on: the whole document, as stored,
    for the results route to filter to the configured companies and rehydrate.
    Ownership is the caller's — `read_analysis` has already established it
    before this is asked.
    """
    statement = sa.select(analyses.c.grade_predictions).where(analyses.c.id == analysis_id)
    result = await execute(db, statement)
    document = result.scalar_one_or_none()
    return None if document is None else dict(document)


async def record_grade_predictions(
    db: AsyncSession,
    analysis_id: UUID,
    *,
    details: dict[str, object],
) -> None:
    """Write the grade prediction step's document onto `analysis_id` — #227.

    `details` is the per-company predictions beside the composed grading
    version and the predictors' thresholds — each company's entry the full
    distribution with its confidence and version, or a one-key
    `insufficient_information` with its reason. The step always writes one,
    so a NULL keeps meaning "the step never ran". Everything
    :func:`record_condition` says about a plain document, one writer and no
    trigger holds here unchanged.
    """
    statement = (
        sa.update(analyses).where(analyses.c.id == analysis_id).values(grade_predictions=details)
    )
    await execute(db, statement)
