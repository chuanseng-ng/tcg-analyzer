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

**No rate limiting here.** Spec §55 names analysis endpoints, and #98 owns both
the limiter and the question of what a 429 body says. This endpoint is unlimited
until it lands.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

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

__all__ = ["SESSION_COOKIE", "AnalysisResponse", "analysis_session", "router"]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analyses", tags=["analyses"])

#: The cookie the anonymous session token rides in. Named for what it is; it
#: carries no information beyond an opaque token.
SESSION_COOKIE: Final = "tcg_session"

#: Reaching the database at all failed. `provider_error` with a `details.reason`,
#: on the same terms `GET /cards/{id}` reports an unreachable catalog.
_UNREACHABLE = "The analysis store could not be reached."

#: One message for four different misses. See the module docstring.
_NOT_FOUND = "No analysis is recorded under that identifier."


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
