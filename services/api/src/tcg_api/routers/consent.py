"""Asking to keep a photograph, and letting it be taken back — issue #148.

Three routes and one rule between them: **the user is asked before anything is
kept, and declining costs nothing.** `GET /training-consent` serves the words;
`POST /analyses/{id}/training-consent` acts on a yes; `DELETE
/training-consent/{code}` undoes it. A no makes no request at all, which is what
"costs nothing" means here — there is no row saying somebody declined, because a
row like that is a record of a person's decision kept forever.

HTTP and nothing else, on `routers/cards.py`'s rule. What a consent *is* lives
in :mod:`tcg_api.datasets.consent`, beside the corpus it writes into.

**The text is served rather than shipped to the browser.** Spec §29's `license`
for approved class 4 is the consent text *by version*, so the row records which
words its grantor read — and a copy of those words in `apps/web` would be a
second answer free to drift from the one on the row. `GET /grading-companies`
serves a scale for the same reason.

**The withdrawal code is a bearer capability**, exactly as spec §68's return
code is: a hundred bits, shown once in one body, stored only as a sha256, never
logged, and sent without a session — weeks later there is none, and spec §53
forbids the account that would otherwise carry the identity. Unknown, mistyped
and already-withdrawn are **one bare 404**, so a well-formed guess learns nothing
a malformed one would not.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_shared.storage import StorageError
from tcg_shared.storage.port import ObjectStorage

from tcg_api import codes
from tcg_api.analysis.images import read_v1_photographs
from tcg_api.analysis.sessions import AnalysisStoreUnavailable
from tcg_api.config import Settings, get_settings
from tcg_api.datasets.annotation import DatasetStoreUnavailable
from tcg_api.datasets.consent import (
    CONSENT_TEXT,
    CONSENT_VERSION,
    NothingToRetain,
    retain,
    withdraw,
)
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.rate_limit import analysis_rate_limit
from tcg_api.routers.analyses import analysis_session, object_storage
from tcg_api.routers.economics import owned_analysis

router = APIRouter(tags=["training consent"])

logger = structlog.get_logger(__name__)

#: The one answer every miss gets on the withdrawal route. Unknown code, a code
#: whose photographs are already gone, and a string that is not a code — told
#: apart nowhere, so that holding a guess and holding a real code look the same
#: until the code is real.
_NOTHING_KEPT: Final = "No photographs are kept under that code."

_NOTHING_TO_KEEP: Final = (
    "This analysis has no photograph to keep, so there is nothing to consent to."
)
_ALREADY_KEPT: Final = (
    "These photographs are already in the training corpus, and the code for them was shown once."
)

_UNREACHABLE: Final = "The dataset store could not be reached."
_ANALYSIS_UNREACHABLE: Final = "The analysis store could not be reached."
_IMAGES_UNREACHABLE: Final = "The image store could not be reached."

#: Slow-moving reference data, like `GET /grading-companies`. The version is what
#: makes caching safe: new words are a new version, so a cached body cannot
#: misrepresent what a later row says its grantor agreed to.
_TEXT_CACHE_CONTROL: Final = "public, max-age=3600"

#: A bearer capability travels in the path on the withdrawal route, and the two
#: bodies name what somebody consented to. Neither may sit in a shared cache.
_CACHE_CONTROL: Final = "no-store"


def _unreachable(reason: str, message: str) -> ApiError:
    """503 `provider_error`, naming which store would not answer."""
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        message,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": reason},
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ConsentTextResponse(BaseModel):
    """What a user is asked, and which version of the asking it is."""

    version: str = Field(
        description=(
            "Recorded as spec §29's `license` on every photograph consented to under "
            "these words. A single word changing is a new version, because a row has "
            "to say which words its grantor actually read."
        ),
        examples=["user-upload-consent-v1.0.0"],
    )
    paragraphs: list[str] = Field(
        description=(
            "The whole of the request, in order, to be rendered verbatim. Do not "
            "summarise it, and do not keep a copy: ADR 0008's interpretive rule 1 is "
            "that silence is not a grant, so a paraphrase that dropped the sentence "
            "about derivative use would not have asked for it."
        )
    )


class WithdrawalCodeResponse(BaseModel):
    """The code, in the only place it will ever appear."""

    withdrawal_code: str = Field(
        description=(
            "Shown once, in this body, and nowhere else ever. The rows store only its "
            "sha256 and cannot produce it again, and it is never logged. Write it "
            "down: it is the only way to withdraw, because spec §54 deletes the "
            "session that would otherwise identify these photographs."
        ),
        examples=["A3KDM-9F2QT-BXWR7-N0HJ5"],
    )
    consent_version: str = Field(
        description="The version of the text these photographs were kept under.",
        examples=["user-upload-consent-v1.0.0"],
    )
    photographs_kept: int = Field(
        description="How many photographs are now in the corpus under this code.",
        examples=[2],
    )


class WithdrawalResponse(BaseModel):
    """What a withdrawal reached, and what it could not."""

    deleted: int = Field(description="Photographs deleted, bytes and row together.", examples=[2])
    kept: int = Field(
        description=(
            "Photographs a published dataset version already names, which stay. Spec "
            "§31 makes a version an immutable record of what a model was trained on, "
            "and the consent text says so before anybody agrees to it."
        ),
        examples=[0],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/training-consent",
    response_model=ConsentTextResponse,
    summary="What a user is asked before a photograph is kept",
    description=(
        "The words themselves, and the version they are recorded under. Render them "
        "verbatim beside an **unchecked** control: ADR 0008 admits a photograph only "
        "where consent is positively recorded, so a pre-ticked box, a default, or a "
        "summary that dropped a clause would each record a grant nobody made.\n\n"
        "Slow-moving reference data — `Cache-Control: public, max-age=3600`. Not "
        "rate-limited, for `GET /grading-companies`' reason: it is the same answer "
        "for everybody and carries nothing about anybody."
    ),
)
async def read_consent_text(response: Response) -> ConsentTextResponse:
    """Serve spec §29's `license` for approved class 4, and the text it names."""
    response.headers["Cache-Control"] = _TEXT_CACHE_CONTROL
    return ConsentTextResponse(version=CONSENT_VERSION, paragraphs=list(CONSENT_TEXT))


@router.post(
    "/analyses/{analysis_id}/training-consent",
    response_model=WithdrawalCodeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Keep this analysis's photographs for training",
    description=(
        "ADR 0008's approved source class 4. Copies the photographs this analysis "
        "stored into the training corpus under spec §29's nine fields — filled at "
        "this moment, from the person consenting, with `redistribution_allowed` "
        "`false` — and returns the code that withdraws them.\n\n"
        "**A copy, not an exemption.** The photographs the analysis used are still "
        "deleted with the session at seven days, and no retention sweep was taught "
        "to skip anything: `docs/retention.md` says a photograph is kept because a "
        "row says so, and this is that row.\n\n"
        "**The code appears in this body and nowhere else, ever.** Only its sha256 is "
        "stored, and it is never logged. A lost code is a photograph nobody can take "
        "back, deliberately: the alternative is keeping something that identifies who "
        "sent it.\n\n"
        "**Once per analysis.** A second request is a 409 — the same photographs "
        "cannot enter the corpus twice (`uq_training_images_sha256`), and the code "
        "was shown once and cannot be reproduced. That constraint is on the bytes "
        "rather than on the analysis, so the identical file consented to twice is "
        "also a 409."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No analysis is recorded under that identifier — for this caller. "
                "The bare 404 `GET /analyses/{id}` answers with."
            )
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "The analysis has stored no photograph, or these photographs are "
                "already in the corpus. Outside the spec §66 taxonomy, which has no "
                "code meaning 'conflict'."
            )
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55). Carries "
                "`Retry-After`. Outside the spec §66 taxonomy — see ADR 0005."
            )
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "A store would not answer. `details.reason` names which: "
                "`analysis_store_unreachable`, `dataset_store_unreachable` or "
                "`image_store_unreachable`."
            ),
        },
    },
)
async def keep_photographs(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    storage: Annotated[ObjectStorage, Depends(object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    analysis_id: Annotated[
        UUID, Path(description="The identifier `POST /analyses` answered with.")
    ],
) -> WithdrawalCodeResponse:
    """Copy the photographs into the corpus under a fresh code, and show it once.

    The analysis is scoped by `owned_analysis`, so consent can only be given for
    photographs this session sent. No status gate beyond that: consent is asked
    on the upload screen, before spec §19's gate has run and before the card is
    identified, which is the earliest honest moment to ask and is why the row
    carries no `card_id`.
    """
    record = await owned_analysis(db, request, analysis_id)

    try:
        photographs = await read_v1_photographs(db, record.id)
    except AnalysisStoreUnavailable as error:
        logger.warning("consent.photographs_could_not_be_read", exc_info=True)
        raise _unreachable("analysis_store_unreachable", _ANALYSIS_UNREACHABLE) from error

    code = codes.mint()
    try:
        kept = await retain(
            db,
            storage,
            analysis_id=record.id,
            photographs=photographs,
            code_hash=codes.digest(code),
            max_bytes=settings.upload_max_bytes,
            max_pixels=settings.upload_max_pixels,
        )
    except NothingToRetain:
        raise HTTPException(status.HTTP_409_CONFLICT, _NOTHING_TO_KEEP) from None
    except IntegrityError:
        # The digest is unique across the corpus, so this is both the double-tap
        # guard and the answer to the same file consented to twice. Caught around
        # the raw connection `retain` uses: `tcg_api.database.execute` would have
        # turned it into a store failure and answered 503.
        logger.info("consent.already_kept", analysis_id=str(record.id))
        raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_KEPT) from None
    except (AnalysisStoreUnavailable, DatasetStoreUnavailable) as error:
        logger.warning("consent.could_not_be_recorded", exc_info=True)
        raise _unreachable("dataset_store_unreachable", _UNREACHABLE) from error
    except StorageError as error:
        logger.warning("consent.photographs_could_not_be_copied", exc_info=True)
        raise _unreachable("image_store_unreachable", _IMAGES_UNREACHABLE) from error

    # How many, and which analysis — never the code, never the digest, and never
    # a storage key. A log line holding any of the three would answer somebody
    # else's question about somebody else's photographs.
    logger.info(
        "consent.photographs_kept",
        analysis_id=str(record.id),
        photographs=len(kept),
        consent_version=CONSENT_VERSION,
    )
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return WithdrawalCodeResponse(
        withdrawal_code=code,
        consent_version=CONSENT_VERSION,
        photographs_kept=len(kept),
    )


@router.delete(
    "/training-consent/{code}",
    response_model=WithdrawalResponse,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Withdraw a consent, and delete what it kept",
    description=(
        "**Reads no session cookie**, and needs none: weeks later it has expired, "
        "the tab is closed, and spec §53 forbids the account that would otherwise "
        "carry the identity. The code is the whole of the authorisation.\n\n"
        "Deletes every photograph the code names, bytes and row together, **except "
        "any a published dataset version already names** — spec §31 makes a version "
        "an immutable record of what a model was trained on, so one inside a version "
        "stays and is counted in `kept`. The consent text says this before anybody "
        "agrees to it.\n\n"
        "**Unknown, mistyped and already-withdrawn are one bare 404.** A page able "
        "to tell them apart would tell a guesser their code was real. There is no "
        "read route to check a code with first, for the same reason."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No photographs are kept under that code — unknown, mistyped, or "
                "already withdrawn, told apart nowhere. Deliberately outside the "
                "spec §66 taxonomy, as every analysis-route 404 is."
            )
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": (
                "Too many requests from this client (spec §55). Carries "
                "`Retry-After`. Outside the spec §66 taxonomy — see ADR 0005."
            )
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "A store would not answer. `details.reason` names which: "
                "`dataset_store_unreachable` or `image_store_unreachable`."
            ),
        },
    },
)
async def withdraw_consent(
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    storage: Annotated[ObjectStorage, Depends(object_storage)],
    code: Annotated[str, Path(description="The code minted when the photographs were kept.")],
) -> WithdrawalResponse:
    """Delete what this code covers, and say what a published version held back."""
    try:
        digest = codes.digest(code)
    except codes.InvalidReturnCode:
        # Answered exactly as an unknown code is: a malformed guess must not be
        # distinguishable from a well-formed one that misses.
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOTHING_KEPT) from None

    try:
        result = await withdraw(db, storage, code_hash=digest)
    except DatasetStoreUnavailable as error:
        logger.warning("consent.could_not_be_withdrawn", exc_info=True)
        raise _unreachable("dataset_store_unreachable", _UNREACHABLE) from error
    except StorageError as error:
        logger.warning("consent.photographs_could_not_be_deleted", exc_info=True)
        raise _unreachable("image_store_unreachable", _IMAGES_UNREACHABLE) from error

    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOTHING_KEPT)

    logger.info("consent.withdrawn", deleted=result.deleted, kept=result.kept)
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return WithdrawalResponse(deleted=result.deleted, kept=result.kept)
