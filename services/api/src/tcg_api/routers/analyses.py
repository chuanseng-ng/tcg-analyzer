"""Starting an analysis, and asking how it is going — spec §64, §53.

Two endpoints, and the first thing in this product that belongs to somebody. V1
requires no login (spec §53), so an anonymous session is the whole of a user's
continuity: `POST /analyses` opens one if the caller has none, and every later
read is scoped to it.

**The session token travels in an HTTP-only cookie.** It is the only thing
separating one anonymous user's photographs from another's, so the browser
holding it where script cannot read it is worth more than the convenience of a
header a client has to remember to send. `SameSite=lax` is enough today —
`apps/web` on :3000 and this service on :8000 are the same site — and a
deployment that splits the two across registrable domains is where a `none`
knob belongs, not here.

**Every miss on `GET /analyses/{id}` is the same 404.** No cookie, a lapsed
cookie, an identifier naming nothing, and an identifier naming somebody else's
analysis all answer identically, because a caller able to tell them apart could
use this endpoint to enumerate which analyses exist. It is FastAPI's own
`HTTPException` rather than a spec §66 envelope: `tcg_api.errors` leaves
transport-level failures alone, and none of §66's eight codes means "not found".
`GET /cards/{id}`'s 404 is not a precedent to copy — `card_not_identified`
genuinely describes a card this deployment cannot identify, and nothing in the
taxonomy describes an analysis that is not yours.

**Running is asynchronous, and `queued` is not a state.** Spec §65 has
`POST /analyses/{id}/run` answer `queued` and then lists nine states without it,
so the row is left exactly where it was and the worker moves it. The transport
word and the record deliberately do not agree, because they are answering
different questions: "did you accept this?" and "where has it got to?".

**No rate limiting here.** Spec §55 names analysis endpoints, and #98 owns both
the limiter and the question of what a 429 body says. These endpoints are
unlimited until it lands.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus

from tcg_api.analysis.jobs import JobQueueUnavailable, enqueue_analysis
from tcg_api.analysis.sessions import (
    AnalysisRecord,
    AnalysisStoreUnavailable,
    create_analysis,
    create_session,
    new_session_token,
    read_analysis,
    resolve_session,
)
from tcg_api.config import get_settings
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.version import application_version

__all__ = [
    "SESSION_COOKIE",
    "AnalysisResponse",
    "AnalysisRunResponse",
    "analysis_session",
    "router",
]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analyses", tags=["analyses"])

#: The cookie the anonymous session token rides in. Named for what it is; it
#: carries no information beyond an opaque token.
SESSION_COOKIE: Final = "tcg_session"

#: Reaching the database at all failed. `provider_error` with a `details.reason`,
#: on the same terms `GET /cards/{id}` reports an unreachable catalog.
_UNREACHABLE = "The analysis store could not be reached."

#: The job queue could not be reached, or was never configured. A different
#: dependency from the store, so a different `details.reason` — an operator
#: reading the log should not have to guess which of the two is down.
_QUEUE_UNREACHABLE = "The analysis could not be queued."

#: One message for four different misses. See the module docstring.
_NOT_FOUND = "No analysis is recorded under that identifier."

#: The one state a run may start from. Spec §18's pipeline begins with images,
#: so an analysis that has none has nothing to analyse. The upload endpoint that
#: puts an analysis here is its own issue; until it lands this is reached only
#: by a test writing the row.
_RUNNABLE: Final = AnalysisStatus.UPLOADED


class AnalysisResponse(BaseModel):
    """One analysis, as the API reports it.

    `apps/web` generates its types from this model (ADR 0001), so the field
    names are a public contract.

    Deliberately small. `session_id` is absent because it is ours and internal —
    the client holds a token, not a row id — and every §57 reproducibility field
    is absent because nothing writes one yet; a column that is always NULL in a
    response is an invitation to render an empty value. #35 adds the states this
    can hold, #104 fills `card_id`.
    """

    id: UUID = Field(description="This service's identifier for the analysis.")
    status: str = Field(
        description=(
            "One of spec §65's nine states. `created` until an upload moves it. "
            "`queued` is a transport word `POST /analyses/{id}/run` answers with "
            "and is never held here."
        ),
        examples=["created"],
    )
    created_at: datetime = Field(description="When the analysis was started.")
    completed_at: datetime | None = Field(
        description="When it reached a terminal state, or null while it has not.",
    )
    card_id: UUID | None = Field(
        description=(
            "The card the user confirmed, or null before they have. Unknown "
            "until confirmation (spec §20), which is a step in the pipeline "
            "rather than a precondition of starting one."
        ),
    )


class AnalysisRunResponse(BaseModel):
    """The acknowledgement `POST /analyses/{id}/run` answers with — spec §65.

    §65 names both fields, and names the first `analysis_id` rather than `id`.
    Transcribed rather than tidied: this is the shape a client was told to
    expect, and `AnalysisResponse` is a different message about a different
    thing.
    """

    analysis_id: UUID = Field(description="The analysis that was queued.")
    status: Literal["queued"] = Field(
        description=(
            "Always `queued`. A transport word meaning 'accepted, not started' — "
            "it is not one of spec §65's nine states and no analysis ever holds "
            "it. Poll `GET /analyses/{id}` for the state the analysis is in."
        ),
    )


def _response(record: AnalysisRecord) -> AnalysisResponse:
    return AnalysisResponse(
        id=record.id,
        status=record.status,
        created_at=record.created_at,
        completed_at=record.completed_at,
        card_id=record.card_id,
    )


def _unreachable() -> ApiError:
    """The one 503 this router raises, so its three sites cannot drift apart."""
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "analysis_store_unreachable"},
    )


def _queue_unreachable() -> ApiError:
    """The 503 for a broker that is down or unconfigured.

    `provider_error` on the same terms as the store's, because it is the same
    kind of failure: the request was well-formed and this deployment cannot
    currently act on it. §66 has no code for "the queue is down", and inventing
    a ninth is a specification change.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _QUEUE_UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "job_queue_unreachable"},
    )


async def analysis_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for one request. A dependency so tests can override it.

    Building the factory sits inside the guard for the reason `card_repository`
    gives: it reads `TCG_API_DATABASE_URL`, and an unset or malformed value
    should be the same 503 as an unreachable database rather than a 500. A
    deployment with no database cannot start an analysis, which is not something
    unexpected having happened.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("analysis.session_factory_unavailable", exc_info=True)
        raise _unreachable() from error

    async with factory() as session:
        yield session


@router.post(
    "",
    response_model=AnalysisResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start an analysis",
    description=(
        "Starts an analysis and, if the caller has no live session, opens one. "
        "No login and no registration: V1 identifies a user by an anonymous "
        "session token only (spec §53), returned in an HTTP-only cookie that "
        "every later call to this analysis must carry. A cookie naming a "
        "session that has expired or never existed is not an error — a new "
        "session is opened. Nothing about the caller is recorded."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The analysis store could not be reached.",
        },
    },
)
async def start_analysis(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
) -> AnalysisResponse:
    """Open an analysis, in the caller's session or in a new one.

    The session and the analysis are written in one transaction, so a failure
    part-way through cannot leave a cookie pointing at a session that was never
    committed.
    """
    settings = get_settings()
    try:
        session_id = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        if session_id is None:
            token = new_session_token()
            session_id, _ = await create_session(
                db,
                token=token,
                ttl_seconds=settings.session_ttl_seconds,
                application_version=application_version(),
            )
            response.set_cookie(
                SESSION_COOKIE,
                token,
                max_age=settings.session_ttl_seconds,
                path="/",
                httponly=True,
                secure=settings.session_cookie_secure,
                samesite="lax",
            )
        record = await create_analysis(db, session_id)
        await db.commit()
    except AnalysisStoreUnavailable as error:
        logger.warning("analysis.could_not_be_started", exc_info=True)
        raise _unreachable() from error

    # The internal session id, never the token, and nothing about the caller —
    # no address, no user agent. Spec §53: an analysis is not tied to a person.
    logger.info("analysis.created", analysis_id=str(record.id), session_id=str(session_id))
    return _response(record)


@router.get(
    "/{analysis_id}",
    response_model=AnalysisResponse,
    summary="Report the state of one analysis",
    description=(
        "Returns the analysis, provided the caller's session cookie is the "
        "session that started it. An identifier that names nothing, an analysis "
        "belonging to another session, a missing cookie and an expired one all "
        "answer 404 with the same body, so this endpoint cannot be used to "
        "discover which analyses exist."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this "
                "caller. Outside the spec §66 taxonomy, which has no code "
                "meaning 'not found'."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The analysis store could not be reached.",
        },
    },
)
async def read_one_analysis(
    request: Request,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    analysis_id: Annotated[
        UUID,
        Path(description="The identifier `POST /analyses` answered with."),
    ],
) -> AnalysisResponse:
    """Return the caller's analysis, or say there is none — without saying which.

    `analysis_id` is typed `UUID`, so a malformed identifier is FastAPI's own
    422, exactly as `GET /cards/{id}` decided for the same case: a malformed
    path segment is a transport-level failure with no §66 meaning.
    """
    try:
        session_id = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        record = None if session_id is None else await read_analysis(db, analysis_id, session_id)
    except AnalysisStoreUnavailable as error:
        logger.warning("analysis.could_not_be_read", exc_info=True)
        raise _unreachable() from error

    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    return _response(record)


@router.post(
    "/{analysis_id}/run",
    response_model=AnalysisRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run an analysis",
    description=(
        "Hands the analysis to a background worker and returns immediately "
        "(spec §8, §65): image processing and inference take far longer than an "
        "HTTP request should. The response says `queued`, which is an "
        "acknowledgement rather than a state — poll `GET /analyses/{id}` to see "
        "where the analysis has got to.\n\n"
        "Only an analysis whose images have arrived can be run; spec §18's "
        "pipeline begins with them. Running one twice is safe: the worker "
        "claims the analysis with a conditional update, so a second job finds "
        "nothing to do rather than repeating the first."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this "
                "caller. The same body `GET /analyses/{id}` answers with, for "
                "the same reason."
            ),
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "The analysis is not in a state a run may start from. Outside "
                "the spec §66 taxonomy, which has no code meaning 'conflict'."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The analysis store or the job queue could not be reached.",
        },
    },
)
async def run_one_analysis(
    request: Request,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    analysis_id: Annotated[
        UUID,
        Path(description="The identifier `POST /analyses` answered with."),
    ],
) -> AnalysisRunResponse:
    """Queue the analysis, having established that it is the caller's and ready.

    Ownership comes from `read_analysis`, so it is the same `WHERE` clause the
    read uses and the same 404 body — a caller who cannot read an analysis must
    not be able to learn it exists by trying to run it.

    The row is deliberately left in `uploaded`. Marking it before the worker had
    it would mean a broker that swallowed the message left an analysis in a
    state nothing would ever move it out of; the worker's own claim is the only
    thing that advances it, and that claim is what makes a duplicate delivery
    harmless.
    """
    try:
        session_id = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        record = None if session_id is None else await read_analysis(db, analysis_id, session_id)
    except AnalysisStoreUnavailable as error:
        logger.warning("analysis.could_not_be_read", exc_info=True)
        raise _unreachable() from error

    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    if record.status != _RUNNABLE:
        # Saying which state it is in is safe here — ownership is already
        # established, and a client polling this analysis can see it anyway.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An analysis is run once its images have arrived, and this one is {record.status}.",
        )

    try:
        job_id = enqueue_analysis(record.id)
    except JobQueueUnavailable as error:
        logger.warning("analysis.could_not_be_queued", exc_info=True)
        raise _queue_unreachable() from error

    logger.info("analysis.queued", analysis_id=str(record.id), job_id=job_id)
    return AnalysisRunResponse(analysis_id=record.id, status="queued")
