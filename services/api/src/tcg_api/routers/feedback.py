"""Spec §68's feedback loop over HTTP — issue #270.

Three routes, and the shape of them is the point.

`POST /analyses/{id}/feedback` is session-scoped like every other analysis
route: a user who is looking at their own results asks to be able to report the
grade later, and gets a code back once. The other two are addressed **by the
code alone**, because weeks later there is no session left — the cookie has
expired, the tab is closed, and spec §53 forbids the account that would
otherwise carry the identity. The code is a bearer capability: it proves
somebody was shown it, and nothing else.

**Unknown, expired and already-answered are one bare 404 on all three.** They go
through one predicate in `store.read_awaiting`, and so does a string that is not
a code at all — `InvalidReturnCode` is caught and answered the same way, so a
well-formed guess learns nothing a malformed one would not. A code is spent by
the answer: there is no edit path, and a wrong grade is a new code from a new
analysis.

**All three are rate-limited**, sharing the analysis bucket (ADR 0005). A
hundred bits of entropy is the defence against guessing a code and the limiter
is what bounds the attempts; neither is sufficient alone, and the GET is the
first read this service limits for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus

from tcg_api import codes
from tcg_api.analysis.sessions import (
    AnalysisStoreUnavailable,
    claim_feedback_mint,
    read_grade_predictions,
)
from tcg_api.config import Settings, get_settings
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.feedback.store import (
    FeedbackRecord,
    FeedbackStoreUnavailable,
    mint_feedback,
    read_awaiting,
    record_answer,
    verify_answer,
)
from tcg_api.rate_limit import analysis_rate_limit
from tcg_api.routers.analyses import analysis_session
from tcg_api.routers.economics import compute_results, owned_analysis

router = APIRouter(tags=["feedback"])

logger = structlog.get_logger(__name__)

#: The one answer every miss gets. Unknown code, expired code, spent code, and a
#: string that is not a code — told apart nowhere, so that holding a guess and
#: holding a real code look the same until the code is real.
_NO_FEEDBACK: Final = "No feedback is recorded under that code."

_NOT_COMPLETED: Final = (
    "A return code is minted once an analysis is complete, and this one is {status}."
)
_ALREADY_MINTED: Final = (
    "A return code has already been minted for this analysis, and it was shown once."
)
_NOTHING_PREDICTED: Final = (
    "This analysis stored no grade prediction, so there is nothing for a later "
    "grade to be compared against."
)

_UNREACHABLE: Final = "The feedback store could not be reached."
_ANALYSIS_UNREACHABLE: Final = "The analysis store could not be reached."

#: This body is served on a bearer capability carried in the path, so it must
#: not sit in any shared cache. `GET /analyses/{id}/results`' header, for a
#: related reason.
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
# Response and request models
# ---------------------------------------------------------------------------
class ReturnCodeResponse(BaseModel):
    """The code, in the only place it will ever appear."""

    return_code: str = Field(
        description=(
            "Shown once, in this body, and nowhere else ever. The row stores only "
            "its sha256 and cannot produce it again, and it is never logged. Write "
            "it down: a lost code is a lost row, deliberately."
        ),
        examples=["A3KDM-9F2QT-BXWR7-N0HJ5"],
    )
    expires_at: datetime = Field(
        description="After this the code addresses nothing, and the row is swept.",
        examples=["2027-03-07T09:15:00Z"],
    )


class FeedbackSnapshotResponse(BaseModel):
    """What was predicted, for the screen that asks what actually happened."""

    card_id: UUID = Field(
        description="The printed card the analysis confirmed. Read it from `/cards/{id}`.",
    )
    predictions: dict[str, Any] = Field(
        description=(
            "The grade prediction document as the worker stored it (#227), copied "
            "whole at mint time — per-company distributions, each model's "
            "confidence, and the versions that dated it."
        ),
    )
    recommended_action: str | None = Field(
        description=(
            "Spec §44's verdict as the user was shown it, or `null` where the "
            "results screen showed none at all."
        ),
    )
    model_bundle_version: str | None = Field(description="Spec §57's model bundle version.")
    grading_rules_version: str | None = Field(description="Spec §57's grading rules version.")
    created_at: datetime = Field(description="When the code was minted.")
    expires_at: datetime = Field(description="When it stops addressing anything.")


class ReportedGradeRequest(BaseModel):
    """Spec §68's question, answered.

    Validated here rather than in the handler, so a grade no company issues is
    FastAPI's own 422 **before** the code is looked up. That ordering is
    deliberate: a bad grade must answer the same way whether or not the code
    exists, or the refusal itself would tell a guesser their code was real.
    """

    grading_company: str = Field(
        description="Which company issued it — `psa`, `tag` or `bgs`.",
        examples=["psa"],
    )
    grade: str | None = Field(
        default=None,
        description=(
            "The grade printed on the slab, as one point on that company's scale. "
            "`null` for a slab that carries a designation in place of a grade."
        ),
        examples=["9"],
    )
    designation: str | None = Field(
        default=None,
        description=(
            "A designation the slab carries — PSA's `authentic` in place of a "
            "grade, BGS's `black_label` on top of a 10."
        ),
        examples=[None],
    )
    certification_number: str | None = Field(
        default=None,
        description=(
            "The number printed on the slab, if the user has it. Optional: this is "
            "a person answering weeks later, not an operator holding the slab."
        ),
        examples=["12345678"],
    )

    @model_validator(mode="after")
    def _is_a_grade_that_company_issues(self) -> ReportedGradeRequest:
        verify_answer(
            grading_company=self.grading_company,
            grade=self.grade,
            designation=self.designation,
        )
        return self


class ReportedGradeResponse(BaseModel):
    """What was recorded, so the screen can say it back."""

    status: str = Field(
        description="`submitted`. An operator reviews it from here.", examples=["submitted"]
    )
    submitted_at: datetime = Field(description="When this answer was recorded.")
    grading_company: str = Field(examples=["psa"])
    grade: str | None = Field(examples=["9"])
    designation: str | None = Field(examples=[None])
    certification_number: str | None = Field(examples=["12345678"])


def _snapshot(record: FeedbackRecord) -> FeedbackSnapshotResponse:
    return FeedbackSnapshotResponse(
        card_id=record.card_id,
        predictions=dict(record.predictions),
        recommended_action=record.recommended_action,
        model_bundle_version=record.model_bundle_version,
        grading_rules_version=record.grading_rules_version,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


async def _live_feedback(db: AsyncSession, code: str) -> FeedbackRecord:
    """The row this code addresses, or the one 404 every miss shares."""
    try:
        digest = codes.digest(code)
    except codes.InvalidReturnCode:
        # Answered exactly as an unknown code is: a malformed guess must not be
        # distinguishable from a well-formed one that misses.
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_FEEDBACK) from None

    try:
        record = await read_awaiting(db, digest)
    except FeedbackStoreUnavailable as error:
        logger.warning("feedback.could_not_be_read", exc_info=True)
        raise _unreachable("feedback_store_unreachable", _UNREACHABLE) from error

    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_FEEDBACK)
    return record


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/analyses/{analysis_id}/feedback",
    response_model=ReturnCodeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Mint a return code over this analysis's prediction",
    description=(
        "Spec §68's way back. The analysis and its predictions are deleted with "
        "the session at seven days and a grading company takes weeks, so this "
        "copies what was predicted — the distributions, the versions and the "
        "recommendation the user was shown — into a row addressed by a code, and "
        "returns the code.\n\n"
        "**The code appears in this body and nowhere else, ever.** Only its sha256 "
        "is stored, it is never logged, and no route returns it again. A lost code "
        "is a lost row.\n\n"
        "**The row carries no image, no session and no address**, and expires on "
        "its own clock (`TCG_API_FEEDBACK_TTL_SECONDS`, 180 days) rather than with "
        "the session — the one exemption in `docs/retention.md`, justified there "
        "before the table existed.\n\n"
        "**Once per analysis.** Only on `completed`, and a second request is a 409: "
        "the code was shown once and cannot be reproduced."
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
                "The analysis is not `completed`, it stored no prediction, or a "
                "code has already been minted for it. Outside the spec §66 "
                "taxonomy, which has no code meaning 'conflict'."
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
                "`analysis_store_unreachable`, `feedback_store_unreachable`, "
                "`economic_configuration_store_unreachable`, "
                "`market_store_unreachable` or `catalog_unreachable`."
            ),
        },
    },
)
async def mint_return_code(
    request: Request,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    analysis_id: Annotated[
        UUID, Path(description="The identifier `POST /analyses` answered with.")
    ],
) -> ReturnCodeResponse:
    """Copy this analysis's prediction under a fresh code, and show the code once.

    The snapshot is a **copy** rather than a reference because the source row is
    swept at seven days: `analyses.grade_predictions` whole, so the versions and
    thresholds that dated it travel with the distributions.

    The recommendation comes from `compute_results`, which is what
    `GET /analyses/{id}/results` renders — so the verdict stored here is the one
    the user was actually shown, rather than a second derivation free to
    disagree with it.
    """
    record = await owned_analysis(db, request, analysis_id)

    # `completed` implies a confirmed card: it is reachable only through
    # `analyzing`, which only `confirm-card` enters and which writes `card_id`
    # in the same transaction. The `card_id` half narrows the type rather than
    # stating a second policy.
    if record.status != AnalysisStatus.COMPLETED.value or record.card_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _NOT_COMPLETED.format(status=record.status))

    try:
        predictions = await read_grade_predictions(db, record.id)
    except AnalysisStoreUnavailable as error:
        logger.warning("feedback.predictions_could_not_be_read", exc_info=True)
        raise _unreachable("analysis_store_unreachable", _ANALYSIS_UNREACHABLE) from error

    if predictions is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _NOTHING_PREDICTED)

    computed = await compute_results(db, record, settings, at=datetime.now(UTC))

    try:
        # The arbiter, not the status read above: two taps both see `completed`
        # and exactly one matches `feedback_minted_at IS NULL`.
        if not await claim_feedback_mint(db, record.id):
            raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_MINTED)

        code = codes.mint()
        stored = await mint_feedback(
            db,
            return_code_hash=codes.digest(code),
            card_id=record.card_id,
            predictions=predictions,
            model_bundle_version=record.model_bundle_version,
            grading_rules_version=record.grading_rules_version,
            recommended_action=(
                None
                if computed.recommendation is None
                else str(computed.recommendation.recommended_action)
            ),
            ttl_seconds=settings.feedback_ttl_seconds,
        )
        await db.commit()
    except (AnalysisStoreUnavailable, FeedbackStoreUnavailable) as error:
        logger.warning("feedback.could_not_be_minted", exc_info=True)
        raise _unreachable("feedback_store_unreachable", _UNREACHABLE) from error

    # The row's identifier and what it will mean — never the code and never the
    # digest, which is the lookup key and would let whoever reads logs answer
    # somebody else's question.
    logger.info(
        "feedback.minted",
        analysis_id=str(record.id),
        feedback_id=str(stored.id),
        recommended_action=stored.recommended_action,
        expires_at=stored.expires_at.isoformat(),
    )
    return ReturnCodeResponse(return_code=code, expires_at=stored.expires_at)


@router.get(
    "/feedback/{code}",
    response_model=FeedbackSnapshotResponse,
    dependencies=[Depends(analysis_rate_limit)],
    summary="What was predicted, for the code a user kept",
    description=(
        "The prediction snapshot the code addresses, so a screen can show what "
        "was predicted beside the question of what actually happened.\n\n"
        "**No session is read.** Weeks later there is none: the cookie has "
        "expired and spec §53 forbids the account that would otherwise carry the "
        "identity. The code is the whole of the authorisation, and holding one "
        "proves that somebody was shown it.\n\n"
        "**A code is spent by the answer.** Unknown, expired, already-answered and "
        "malformed are one bare 404, so a well-formed guess learns nothing a "
        "malformed one would not."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No feedback is recorded under that code — unknown, expired, "
                "already answered, or not a code at all. All four, deliberately "
                "indistinguishable."
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
            "description": "`details.reason` is `feedback_store_unreachable`.",
        },
    },
)
async def read_feedback(
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    code: Annotated[
        str,
        Path(
            description=(
                "The return code, as it was shown or as a person wrote it down — "
                "case, hyphens and spacing are all forgiven."
            ),
            examples=["A3KDM-9F2QT-BXWR7-N0HJ5"],
        ),
    ],
) -> FeedbackSnapshotResponse:
    """Serve the snapshot, and never anything about who is asking."""
    record = await _live_feedback(db, code)
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return _snapshot(record)


@router.post(
    "/feedback/{code}",
    response_model=ReportedGradeResponse,
    dependencies=[Depends(analysis_rate_limit)],
    summary="Report the grade this card actually received",
    description=(
        "Spec §68's answer. Recorded against the prediction the code addresses, "
        "and **not** used to retrain anything: §68 puts a validation step in "
        "between, and that step is an operator reading the row by hand.\n\n"
        "**Once.** The code is spent by this call, so a second answer is the same "
        "404 an unknown code gets. There is no edit path — a wrong grade is a new "
        "code from a new analysis.\n\n"
        "**The grade is checked against that company's scale** — PSA and TAG issue "
        "no 9.5 and BGS does — and a grade off the scale is a 422 whether or not "
        "the code exists, so the refusal cannot be used to test a guess. A "
        "designation with no grade is a whole answer: PSA issues `authentic` in "
        "place of a grade."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No feedback is recorded under that code — unknown, expired, "
                "already answered, or not a code at all."
            )
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The report names no grade and no designation, or names a grade "
                "that company does not issue. FastAPI's own validation shape, "
                "outside the spec §66 taxonomy."
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
            "description": "`details.reason` is `feedback_store_unreachable`.",
        },
    },
)
async def report_grade(
    response: Response,
    db: Annotated[AsyncSession, Depends(analysis_session)],
    code: Annotated[
        str,
        Path(description="The return code.", examples=["A3KDM-9F2QT-BXWR7-N0HJ5"]),
    ],
    reported: ReportedGradeRequest,
) -> ReportedGradeResponse:
    """Record the answer, once.

    `record_answer` matches the same predicate the read does, so the conditional
    `UPDATE` is the race guard as well as the rule: a second answer matches no
    row and is the 404 an unknown code gets.
    """
    await _live_feedback(db, code)

    try:
        stored = await record_answer(
            db,
            return_code_hash=codes.digest(code),
            grading_company=reported.grading_company,
            grade=reported.grade,
            designation=reported.designation,
            certification_number=reported.certification_number,
        )
        if stored is None:
            # Answered between the read and the write. The same 404.
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_FEEDBACK)
        await db.commit()
    except FeedbackStoreUnavailable as error:
        logger.warning("feedback.could_not_be_recorded", exc_info=True)
        raise _unreachable("feedback_store_unreachable", _UNREACHABLE) from error

    # The grade is a fact about a printed card and spec §67 wants it. The
    # certification number stays off the line: it is the one field that joins to
    # a grading company's own registry, which names a submitter.
    logger.info(
        "feedback.answered",
        feedback_id=str(stored.id),
        grading_company=stored.grading_company,
        grade=stored.grade,
        designation=stored.designation,
        certification_number_supplied=stored.certification_number is not None,
    )

    response.headers["Cache-Control"] = _CACHE_CONTROL
    # Both are nullable on the row and neither can be null here:
    # `answer_is_recorded_exactly_when_answered` refuses an answered row without
    # them, so the UPDATE that returned this one could not have left them unset.
    return ReportedGradeResponse(
        status=stored.status,
        submitted_at=cast(datetime, stored.submitted_at),
        grading_company=cast(str, stored.grading_company),
        grade=stored.grade,
        designation=stored.designation,
        certification_number=stored.certification_number,
    )
