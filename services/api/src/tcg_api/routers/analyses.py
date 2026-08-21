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

**The three writes are rate-limited; the poll is not.** Spec §55 names analysis
endpoints *and image uploads*, and #98 reads the first as the endpoints that
create work — starting an analysis, adding a photograph to one, and running it.
`GET /analyses/{id}` is what spec §65 requires a client to poll, so throttling
it would throttle the product's own progress reporting. A throttled request is a
429 with `Retry-After`, outside the §66 envelope for the same reason the 404 and
the 409 above are (ADR 0005).

**The upload takes the image as the request body, not as a multipart part.**
`POST /analyses/{id}/images?side=front` with the bytes and their type in the
body is what a browser sends with `fetch(url, {method: "POST", body: file})`,
and it means **no client filename ever arrives**. Spec §55 asks for filenames to
be sanitised and for storage paths to be generated server-side; having no
parameter through which a name could arrive is the structural version of both,
exactly as `tcg_shared.storage.keys.generate_key` takes no filename argument. It
also lets the byte limit be applied while the body is still being read, rather
than after a multipart parser has already spooled whatever was sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from tcg_domain.analysis import V1_SIDES, AnalysisStatus, ImageSide
from tcg_shared.storage import ObjectStorage, StorageError, StorageKey, generate_key

from tcg_api.analysis.image_validation import InvalidImage, ValidatedImage, validate_image
from tcg_api.analysis.images import ImageRecord, read_image_key, upsert_image, v1_sides_present
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
from tcg_api.analysis.state import transition
from tcg_api.config import get_settings
from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.rate_limit import analysis_rate_limit
from tcg_api.storage import get_object_storage
from tcg_api.version import application_version

__all__ = [
    "SESSION_COOKIE",
    "UPLOAD_NAMESPACE",
    "AnalysisResponse",
    "AnalysisRunResponse",
    "ImageResponse",
    "UploadSide",
    "analysis_session",
    "object_storage",
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
#: so an analysis that has none has nothing to analyse. `POST
#: /analyses/{id}/images` is what puts an analysis here, once both of spec §11's
#: V1 sides have arrived.
_RUNNABLE: Final = AnalysisStatus.UPLOADED

#: The object store could not be reached, or is not configured. A third
#: `details.reason`, distinct from the store's and the queue's, so an operator
#: reading a 503 is told which of three dependencies is down rather than
#: guessing.
_IMAGES_UNREACHABLE = "The image could not be stored."

#: The prefix every uploaded photograph is stored under. `generate_key` adds a
#: `YYYY/MM/DD` partition beneath it, which is what makes spec §54's retention
#: sweep a prefix scan rather than a listing of the whole bucket.
UPLOAD_NAMESPACE: Final = "uploads"

#: The sides a V1 upload may name. A `Literal` rather than `ImageSide`, which
#: admits all six of spec §11's values: the schema accepts the four
#: guided-photography sides from the first migration (§52) and no V1 pipeline
#: stage can process one, so the endpoint must not take an image it would then
#: have nowhere to send. `test_image_validation.py` asserts these two members
#: are exactly `tcg_domain.analysis.V1_SIDES`, on the precedent
#: `analysis/tables.py` sets for a literal that shadows a vocabulary.
UploadSide = Literal["front", "back"]

#: The states an image may still be added in. Before the first upload, and
#: between the two, and for a retake once both have arrived. Anything later
#: means the analysis is in flight or finished, and changing its inputs then
#: would change what a running job is working from.
_ACCEPTS_IMAGES: Final = frozenset(
    {AnalysisStatus.CREATED, AnalysisStatus.UPLOADING, AnalysisStatus.UPLOADED}
)


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


class ImageResponse(BaseModel):
    """One uploaded photograph, as the API reports it.

    `apps/web` generates its types from this model (ADR 0001), so the field
    names are a public contract.

    The derived columns — `normalized_uri`, `width`, `height`, `quality_score`,
    `quality_status` — are deliberately absent: nothing has computed one yet,
    and a field that is always null is an invitation to render an empty value.
    `analysis_status` is present because the caller has just caused the
    transition and would otherwise need a second request to learn whether the
    analysis can now be run.
    """

    id: UUID = Field(description="This service's identifier for the image.")
    analysis_id: UUID = Field(description="The analysis this photograph belongs to.")
    side: str = Field(description="Which view of the card this is.", examples=["front"])
    mime_type: str = Field(
        description=(
            "The type the file was validated as, read from its content. Never "
            "the type the request declared (spec §55)."
        ),
        examples=["image/jpeg"],
    )
    sha256: str = Field(
        description=(
            "A digest over the bytes that were stored — which are the uploaded "
            "bytes with their metadata removed, not the bytes as they arrived."
        ),
    )
    created_at: datetime = Field(description="When this photograph was accepted.")
    analysis_status: str = Field(
        description=(
            "The analysis's state after this upload. `uploading` until every "
            "side has arrived, then `uploaded`, which is the state "
            "`POST /analyses/{id}/run` requires."
        ),
        examples=["uploading"],
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


def _images_unreachable() -> ApiError:
    """The 503 for an object store that is down or unconfigured.

    `provider_error` on the same terms as the store's and the queue's, with its
    own `details.reason`. The request was well-formed and this deployment cannot
    currently act on it; §66 has no code for "the bucket is missing".
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _IMAGES_UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "image_store_unreachable"},
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


async def object_storage() -> ObjectStorage:
    """Yield the object store for one request. A dependency so tests can override it.

    Construction sits inside the guard for the reason `analysis_session` gives:
    `get_object_storage` reads the `TCG_API_STORAGE_*` variables, and an unset
    bucket or key should be the same 503 as an unreachable store rather than a
    500. A deployment with no bucket cannot accept a photograph, which is not
    something unexpected having happened.
    """
    try:
        return get_object_storage()
    except Exception as error:
        logger.warning("analysis.object_storage_unavailable", exc_info=True)
        raise _images_unreachable() from error


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
    dependencies=[Depends(analysis_rate_limit)],
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55). Carries "
                "`Retry-After`. Outside the spec §66 taxonomy, which has no "
                "code meaning 'throttled' — see ADR 0005."
            ),
        },
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
    dependencies=[Depends(analysis_rate_limit)],
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
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55). Carries "
                "`Retry-After`. Outside the spec §66 taxonomy, which has no "
                "code meaning 'throttled' — see ADR 0005."
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


async def _read_body(request: Request, max_bytes: int) -> bytes:
    """Read the request body, refusing it the moment it exceeds `max_bytes`.

    Read from the stream rather than with `await request.body()`, so the limit
    is applied *while* the upload arrives and memory is bounded by the limit
    rather than by whatever the client chose to send. `Content-Length` is not
    consulted: it is the client's claim about the body, and a chunked request
    does not carry one at all.
    """
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_bytes:
            raise ApiError(
                ErrorCode.INVALID_IMAGE,
                f"The image is larger than {max_bytes:,} bytes.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    return bytes(body)


async def _advance_after_upload(
    db: AsyncSession,
    record: AnalysisRecord,
    *,
    sides_present: int,
) -> str:
    """Move the analysis on, if this upload is what moved it, and say where it is.

    Two steps rather than one, because spec §65's graph has no shortcut from
    `created` to `uploaded` and no self-edges: the first photograph starts the
    upload, and the last one finishes it. Both go through
    `tcg_api.analysis.state.transition`, so an illegal move is refused by the
    same `WHERE` clause that performs a legal one and two concurrent uploads
    cannot both claim to be the first.

    An analysis already in `uploaded` — a retake — stays there. It is still true
    that every side has arrived, and `uploaded -> uploading` is not a move the
    graph allows in any case.
    """
    status_now = record.status
    if status_now == AnalysisStatus.CREATED and await transition(
        db, record.id, to=AnalysisStatus.UPLOADING
    ):
        status_now = AnalysisStatus.UPLOADING

    if (
        sides_present >= len(V1_SIDES)
        and status_now == AnalysisStatus.UPLOADING
        and await transition(db, record.id, to=AnalysisStatus.UPLOADED)
    ):
        status_now = AnalysisStatus.UPLOADED

    return status_now


@router.post(
    "/{analysis_id}/images",
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Upload one side of the card",
    description=(
        "Accepts one photograph as the request body — the raw image bytes, not "
        "a multipart form. The `side` says which view of the card it is; a "
        "second upload for the same side replaces the first, so a retake is a "
        "correction rather than an extra image.\n\n"
        "**The file is validated by its content, never by what the request "
        "claims** (spec §55). It must be a JPEG or a PNG, must be within the "
        "byte and pixel limits this deployment is configured with, and must "
        "decode. Personal metadata — EXIF, including GPS — is removed before "
        "anything is stored, and the storage location is generated by the "
        "server: no filename is accepted, so none can influence it.\n\n"
        "The analysis moves to `uploading` on the first photograph and to "
        "`uploaded` once both sides have arrived, which is the state "
        "`POST /analyses/{id}/run` requires."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": (
                "The upload is not an image this service accepts. Always "
                "`invalid_image`; the message says which rule was broken and "
                "nothing about how the decoder failed."
            ),
        },
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this "
                "caller. The same body `GET /analyses/{id}` answers with, for "
                "the same reason."
            ),
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "The analysis has moved past the point where its images can "
                "change. Outside the spec §66 taxonomy, which has no code "
                "meaning 'conflict'."
            ),
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55, which names "
                "image uploads explicitly). Carries `Retry-After`. Outside the "
                "spec §66 taxonomy — see ADR 0005."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The analysis store or the image store could not be reached.",
        },
    },
)
async def upload_image(
    request: Request,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    storage: Annotated[ObjectStorage, Depends(object_storage)],
    analysis_id: Annotated[
        UUID,
        Path(description="The identifier `POST /analyses` answered with."),
    ],
    side: Annotated[
        UploadSide,
        Query(description="Which view of the card this photograph is."),
    ],
) -> ImageResponse:
    """Validate a photograph, store it, and record it against the analysis.

    Ownership is established before a single byte is read, through the same
    `read_analysis` the other routes use — so an unknown identifier, another
    session's analysis, a missing cookie and an expired one are one 404 with one
    body here too. This endpoint must not become the one that tells a caller
    which analyses exist.

    The order of effects is deliberate. The object is written before the row, so
    a committed row always names bytes that are there; if the commit then fails
    the object is deleted, because an object no row points at is invisible to a
    retention sweep that works from rows (spec §54, #41). A retake's superseded
    object is deleted after the commit, for the same reason.
    """
    settings = get_settings()
    try:
        session_id = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        record = None if session_id is None else await read_analysis(db, analysis_id, session_id)
    except AnalysisStoreUnavailable as error:
        logger.warning("analysis.could_not_be_read", exc_info=True)
        raise _unreachable() from error

    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    if record.status not in _ACCEPTS_IMAGES:
        # Safe to name the state: ownership is established, and the caller can
        # already see it by polling.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An analysis takes photographs until it runs, and this one is {record.status}.",
        )

    body = await _read_body(request, settings.upload_max_bytes)
    try:
        # Off the event loop: decoding tens of megapixels is CPU-bound, and a
        # blocking call on the loop is an outage under load rather than a slow
        # request.
        validated: ValidatedImage = await run_in_threadpool(
            validate_image, body, max_pixels=settings.upload_max_pixels
        )
    except InvalidImage as error:
        # The message is the rule that was broken. The decoder's own exception
        # goes to the log and never into the body.
        logger.info("image.rejected", analysis_id=str(record.id), side=side, exc_info=True)
        raise ApiError(
            ErrorCode.INVALID_IMAGE,
            str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from error

    image_side = ImageSide(side)
    key = generate_key(UPLOAD_NAMESPACE)
    try:
        superseded = await read_image_key(db, record.id, image_side)
    except AnalysisStoreUnavailable as error:
        logger.warning("image.could_not_be_recorded", exc_info=True)
        raise _unreachable() from error

    try:
        await storage.put(key, validated.data, content_type=validated.mime_type)
    except StorageError as error:
        logger.warning("image.could_not_be_stored", exc_info=True)
        raise _images_unreachable() from error

    try:
        stored: ImageRecord = await upsert_image(
            db,
            analysis_id=record.id,
            side=image_side,
            original_uri=str(key),
            mime_type=validated.mime_type,
            sha256=validated.sha256,
        )
        analysis_status = await _advance_after_upload(
            db, record, sides_present=await v1_sides_present(db, record.id)
        )
        await db.commit()
    except AnalysisStoreUnavailable as error:
        await _discard(storage, key)
        logger.warning("image.could_not_be_recorded", exc_info=True)
        raise _unreachable() from error
    except Exception:
        # Blind on purpose, and it re-raises. `db.commit()` raises the driver's
        # own error rather than one `sessions.execute` has translated, and
        # whatever goes wrong here the object that was just written must not be
        # left behind with no row naming it.
        await _discard(storage, key)
        raise

    if superseded is not None and superseded != str(key):
        await _discard(storage, StorageKey(superseded))

    # The internal identifiers and the digest; nothing about the caller and
    # nothing about the picture (spec §53, §54).
    logger.info(
        "image.uploaded",
        analysis_id=str(record.id),
        image_id=str(stored.id),
        side=stored.side,
        mime_type=stored.mime_type,
        bytes=len(validated.data),
        analysis_status=analysis_status,
    )
    return ImageResponse(
        id=stored.id,
        analysis_id=record.id,
        side=stored.side,
        mime_type=stored.mime_type,
        sha256=stored.sha256,
        created_at=stored.created_at,
        analysis_status=analysis_status,
    )


async def _discard(storage: ObjectStorage, key: StorageKey) -> None:
    """Delete an object nothing points at any more, without failing the request.

    Two callers, both about orphans: a row that could not be committed, and the
    photograph a retake replaced. Neither is worth turning into an error a user
    sees — the upload either did or did not happen, and this is housekeeping —
    but both are worth a log line, because an object no row names is one a
    retention sweep working from rows will never find (spec §54, #41).
    """
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("image.orphaned", key=str(key), exc_info=True)
