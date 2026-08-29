"""The internal annotation surface — spec §30's tool, and nothing a consumer sees.

**Not part of spec §64.** §64's endpoints are the consumer product; this is the
internal surface `apps/annotation` reads, and ADR 0009 fixed both halves of what
that means. It is *in this application*, because §7 says not to create
unnecessary microservices in V1 and a second FastAPI application would duplicate
the error-envelope and migration wiring in order to enforce a boundary the
deployment already enforces. It is *in this schema*, because ADR 0001 makes the
OpenAPI document the only sanctioned way a TypeScript application learns an API
shape, and `apps/annotation` generates its types from it exactly as `apps/web`
does. What keeps it internal is neither of those: it is the `/internal` prefix,
which is what an ingress rule matches, and the tool being unroutable from the
public origin.

The router holds HTTP and nothing else — the statements live in
`tcg_api.datasets.annotation`, on `routers/grading.py`'s and `routers/cards.py`'s
rule.

**Three reads and no writes.** #160 adds the writes into `image_annotations` and
`centering_measurements`; this issue ends at the annotator being able to see the
card properly.

Not rate-limited. Spec §55 names the analysis endpoints and the uploads, and ADR
0005 decided a read is neither — an internal tool behind its own ingress is
further from that reasoning still.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import ObjectNotFound, StorageError
from tcg_shared.storage.port import ObjectStorage

from tcg_api.database import get_session_factory
from tcg_api.datasets.annotation import (
    ARTIFACT_MEDIA_TYPE,
    DatasetStoreUnavailable,
    TrainingImageDetail,
    TrainingImageSummary,
    read_bytes,
    read_image,
    read_work_list,
)
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.storage import get_object_storage

__all__ = [
    "AnnotationImageResponse",
    "AnnotationImageSummary",
    "AnnotationWorkListResponse",
    "router",
]

#: structlog rather than the stdlib logger the other read-only routers use,
#: because this one logs a *value*. `ProcessorFormatter`'s chain carries no
#: `ExtraAdder`, so a stdlib `extra` mapping is silently dropped and the line
#: arrives with no identifier on it — which is the whole point of the one below.
#: `routers/analyses.py` and `routers/economics.py` log values the same way.
logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/annotation", tags=["internal: annotation"])

#: Repeated at the head of every route description. A reader arriving at
#: `/docs` from anywhere should not have to work out which of these is §64's.
_INTERNAL: Final = (
    "**Not part of spec §64.** The internal annotation surface (ADR 0009) — in "
    "this application and in this schema because `apps/annotation` generates its "
    "types from it, and kept off the public origin by deployment topology rather "
    "than by being a second service. "
)

_UNREACHABLE: Final = "The training image corpus could not be read."
_IMAGES_UNREACHABLE: Final = "The image store could not be reached."
_MISSING_OBJECT: Final = "The stored image could not be read."
_NO_SUCH_IMAGE: Final = "No such training image."

#: The bytes are an internal tool's, and an artifact is small enough that a
#: refetch on navigation costs nothing worth a caching bug. `no-store` rather
#: than a `max-age` for the reason `docs/development.md` gives about signed
#: URLs: the fewer copies of a training photograph exist outside the store, the
#: fewer places ADR 0008's withdrawal has to reach.
_CACHE_CONTROL: Final = "private, no-store"


class AnnotationImageSummary(BaseModel):
    """One training image, as the work list and an image's siblings report it."""

    id: UUID = Field(description="The training image's identifier.")
    side: str = Field(
        description=(
            "Which view of the card this is — spec §30's front/back, and the same "
            "vocabulary an uploaded analysis uses. Six values, not two: a corpus may "
            "hold angled and surface views of the same copy."
        ),
        examples=["front"],
    )
    card_id: UUID | None = Field(
        default=None,
        description="Which catalog card it depicts, or null where nobody has identified it.",
    )
    physical_copy_id: UUID | None = Field(
        default=None,
        description=(
            "Which physical object it is a photograph of. **Null is an honest answer**: "
            "a consented upload identifies no copy (ADR 0008's approved class 4)."
        ),
    )
    source: str = Field(
        description="Which ADR 0008 source class it came from.",
        examples=["first_party"],
    )
    created_at: datetime = Field(description="When the image was ingested.")
    has_artifact: bool = Field(
        description=(
            "Whether a standardized artifact has been stored for it. False means the "
            "normalization pass has not run, or found no card — the tool then shows the "
            "photograph and must say so, because a coordinate taken against a photograph "
            "is not comparable with one taken against an artifact. The storage key itself "
            "is deliberately not reported: it is server-generated and internal (spec §55)."
        )
    )


class AnnotationWorkListResponse(BaseModel):
    """The images awaiting annotation, one page at a time."""

    images: list[AnnotationImageSummary] = Field(description="This page of images, oldest first.")
    total: int = Field(
        description=(
            "How many images await annotation in total. **This number falls as "
            "annotations land**, so a page boundary can move underneath a client that "
            "is annotating while it pages."
        )
    )
    limit: int = Field(description="The page size that was applied.")
    offset: int = Field(description="The offset that was applied.")


class AnnotationImageResponse(BaseModel):
    """One training image, with the other photographs of the same physical copy."""

    id: UUID = Field(description="The training image's identifier.")
    side: str = Field(description="Which view of the card this is.", examples=["front"])
    card_id: UUID | None = Field(default=None, description="Which catalog card it depicts.")
    physical_copy_id: UUID | None = Field(
        default=None, description="Which physical object it is a photograph of."
    )
    source: str = Field(description="Which ADR 0008 source class it came from.")
    created_at: datetime = Field(description="When the image was ingested.")
    width: int = Field(description="The stored **photograph's** width in pixels.")
    height: int = Field(description="The stored **photograph's** height in pixels.")
    has_artifact: bool = Field(
        description=(
            "Whether a standardized artifact was stored, and therefore which "
            "representation `…/bytes` can serve. False means all there is to show is "
            "the photograph — and a tool showing it must label it, because coordinates "
            "cannot be taken against it. The same field every summary carries, so a "
            "detail is a summary with more on it rather than a second shape."
        )
    )
    siblings: list[AnnotationImageSummary] = Field(
        description=(
            "Other photographs of the same physical copy — what the front/back toggle "
            "moves between. **Empty when `physical_copy_id` is null**, which is an honest "
            "answer rather than a gap: treating null as a group would make every "
            "consented upload a sibling of every other one."
        )
    )


def _summary(image: TrainingImageSummary) -> AnnotationImageSummary:
    return AnnotationImageSummary(
        id=image.id,
        side=image.side,
        card_id=image.card_id,
        physical_copy_id=image.physical_copy_id,
        source=image.source,
        created_at=image.created_at,
        has_artifact=image.has_artifact,
    )


def _detail(image: TrainingImageDetail) -> AnnotationImageResponse:
    return AnnotationImageResponse(
        id=image.id,
        side=image.side,
        card_id=image.card_id,
        physical_copy_id=image.physical_copy_id,
        source=image.source,
        created_at=image.created_at,
        width=image.width,
        height=image.height,
        has_artifact=image.has_artifact,
        siblings=[_summary(sibling) for sibling in image.siblings],
    )


def _corpus_unreachable() -> ApiError:
    """The 503 for a dataset store that is down or unconfigured.

    Its own `details.reason`, distinct from the analysis store's and the
    catalog's: an operator reading a 503 should be told which dependency is not
    answering rather than guessing.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "dataset_store_unreachable"},
    )


def _images_unreachable() -> ApiError:
    """The 503 for an object store that is down or unconfigured.

    `routers/analyses.py`'s reason string reused rather than a seventh invented:
    it is the same store failing in the same way, and two names for it would
    make a log harder to read rather than more precise.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _IMAGES_UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "image_store_unreachable"},
    )


def _not_found() -> HTTPException:
    """The 404 for an image this corpus does not hold.

    FastAPI's own `HTTPException` and deliberately outside the §66 envelope, on
    `GET /analyses/{id}`'s reasoning: none of the eight codes means "not found",
    and `card_not_identified` is about a *card* rather than a precedent for one.
    The taxonomy stays closed at eight.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_IMAGE)


async def annotation_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for one request. A dependency so tests can override it.

    Building the factory sits inside the guard on `analysis_session`'s reasoning:
    it reads `TCG_API_DATABASE_URL`, and an unset or malformed value should be the
    same 503 as an unreachable database rather than a 500.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("annotation.session_factory_unavailable", exc_info=True)
        raise _corpus_unreachable() from error

    async with factory() as session:
        yield session


async def object_storage() -> ObjectStorage:
    """Yield the object store for one request. A dependency so tests can override it."""
    try:
        return get_object_storage()
    except Exception as error:
        logger.warning("annotation.object_storage_unavailable", exc_info=True)
        raise _images_unreachable() from error


@router.get(
    "/images",
    response_model=AnnotationWorkListResponse,
    summary="List the training images awaiting annotation",
    description=(
        _INTERNAL + "Lists the training images that carry neither a defect marker nor a "
        "centering measurement, oldest first. Both tables are checked, not one: spec §30's "
        "eleven features are split across two of them, so an image carrying only a "
        "measurement has been worked on. Ordered by `(created_at, id)` — a total order, so "
        "paging neither drops nor duplicates a row. An offset past the end is an empty page, "
        "never a 404."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus could not be read.",
        },
    },
)
async def list_images_awaiting_annotation(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200, description="How many images to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="How many to skip.")] = 0,
) -> AnnotationWorkListResponse:
    try:
        work = await read_work_list(db, limit=limit, offset=offset)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return AnnotationWorkListResponse(
        images=[_summary(image) for image in work.images],
        total=work.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/images/{image_id}",
    response_model=AnnotationImageResponse,
    summary="Read one training image and the other views of its copy",
    description=(
        _INTERNAL + "Returns one image, which representation can be shown for it, and the "
        "other photographs of the same physical copy — what a front/back toggle moves "
        "between. `siblings` is empty where the image names no physical copy, which is an "
        "honest answer rather than a gap."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such training image."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus could not be read.",
        },
    },
)
async def read_training_image(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    response: Response,
    image_id: Annotated[UUID, Path(description="The training image's identifier.")],
) -> AnnotationImageResponse:
    try:
        image = await read_image(db, image_id)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error

    if image is None:
        raise _not_found()

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return _detail(image)


@router.get(
    "/images/{image_id}/bytes",
    response_class=Response,
    summary="Serve one representation of a training image",
    description=(
        _INTERNAL + "Serves the bytes themselves, read through ADR 0002's `ObjectStorage` "
        "port. `representation=normalized` is the standardized artifact and 404s where none "
        "was stored — deliberately, rather than substituting the photograph: the caller has "
        "already been told which representation exists, and a silent substitution would hand "
        "a client a frame whose coordinates mean nothing. `Cache-Control: private, no-store`."
    ),
    responses={
        status.HTTP_200_OK: {
            "content": {"image/png": {}, "image/jpeg": {}},
            "description": "The stored bytes.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "No such training image, or no such representation of it."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus or the image store could not be reached.",
        },
    },
)
async def read_training_image_bytes(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    storage: Annotated[ObjectStorage, Depends(object_storage)],
    image_id: Annotated[UUID, Path(description="The training image's identifier.")],
    representation: Annotated[
        Literal["normalized", "original"],
        Query(description="Which representation to serve."),
    ] = "normalized",
) -> Response:
    try:
        stored = await read_bytes(db, storage, image_id, representation=representation)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error
    except ObjectNotFound as error:
        # The row names bytes the store does not hold. That will not come right
        # on a retry, so it is emphatically not a 503: it is the two stores
        # disagreeing, which is what `internal_error` means.
        logger.error("annotation.stored_object_missing", image_id=str(image_id))
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            _MISSING_OBJECT,
            details={"reason": "stored_object_missing"},
        ) from error
    except StorageError as error:
        raise _images_unreachable() from error

    if stored is None:
        raise _not_found()

    return Response(
        content=stored.data,
        media_type=stored.media_type or ARTIFACT_MEDIA_TYPE,
        headers={"Cache-Control": _CACHE_CONTROL},
    )
