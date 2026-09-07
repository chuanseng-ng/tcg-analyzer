"""Why an analysis failed, as a closed vocabulary — issue #265, spec §54, §66.

No database. A failure reason is a stored fact chosen from `FailureReason`, and
the two things worth pinning without a store are how an exception on the job
runner's path chooses one, and that the state machine refuses to write `failed`
without one — or a reason without `failed`. Nothing here reads a message: the
mapping keys on the exception's *type*, which is rule 3 of `jobs.py` applied to
the row.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from tcg_api.analysis.failures import FailureReason, failure_reason
from tcg_api.analysis.sessions import AnalysisStoreUnavailable
from tcg_api.analysis.state import transition
from tcg_api.errors import ErrorCode
from tcg_api.grading.rules import GradingRulesUnavailable
from tcg_domain.analysis import AnalysisStatus
from tcg_domain.errors import CatalogUnavailable, InvalidConditionAssessment
from tcg_grading_companies.errors import GradePredictionFailed, GradingCompanyError
from tcg_shared.storage.errors import StorageError, StorageUnavailable


def test_the_vocabulary_is_the_issues_eight() -> None:
    assert {reason.value for reason in FailureReason} == {
        "unusable_photograph",
        "catalog_unavailable",
        "grading_rules_unavailable",
        "image_store_unavailable",
        "model_failed",
        "job_dead_lettered",
        "timed_out",
        "stalled",
    }


def test_only_an_unusable_photograph_is_the_users_to_fix() -> None:
    """Spec §66's two codes: the gate refusing is the one the user can act on."""
    assert FailureReason.UNUSABLE_PHOTOGRAPH.code is ErrorCode.IMAGE_QUALITY_FAILURE
    for reason in FailureReason:
        if reason is not FailureReason.UNUSABLE_PHOTOGRAPH:
            assert reason.code is ErrorCode.ANALYSIS_FAILED, reason


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CatalogUnavailable("down"), FailureReason.CATALOG_UNAVAILABLE),
        (GradingRulesUnavailable("down"), FailureReason.GRADING_RULES_UNAVAILABLE),
        (StorageError("down"), FailureReason.IMAGE_STORE_UNAVAILABLE),
        (StorageUnavailable("down"), FailureReason.IMAGE_STORE_UNAVAILABLE),
        (GradingCompanyError("broke"), FailureReason.MODEL_FAILED),
        (GradePredictionFailed("broke"), FailureReason.MODEL_FAILED),
        (InvalidConditionAssessment("broke"), FailureReason.MODEL_FAILED),
        (SoftTimeLimitExceeded(), FailureReason.TIMED_OUT),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_each_dependency_that_broke_names_its_reason(
    error: Exception, expected: FailureReason
) -> None:
    assert failure_reason(error) is expected


@pytest.mark.parametrize(
    "error",
    [ValueError("anything"), ConnectionError("bare"), AnalysisStoreUnavailable("down")],
    ids=["a_value_error", "a_bare_connection_error", "the_analysis_store"],
)
def test_an_error_the_vocabulary_does_not_name_is_dead_lettered(error: Exception) -> None:
    """`ConnectionError` is every store's base class, so it names none of them.

    The analysis store is the one whose outage `_fail` itself cannot record,
    which is why it has no reason of its own.
    """
    assert failure_reason(error) is FailureReason.JOB_DEAD_LETTERED


def test_a_move_to_failed_needs_a_reason() -> None:
    """Refused before any statement is built, so no session is needed."""
    with pytest.raises(ValueError, match="failed"):
        asyncio.run(transition(None, uuid.uuid4(), to=AnalysisStatus.FAILED))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "to",
    [status for status in AnalysisStatus if status is not AnalysisStatus.FAILED],
    ids=[status.value for status in AnalysisStatus if status is not AnalysisStatus.FAILED],
)
def test_a_reason_belongs_only_to_a_move_to_failed(to: AnalysisStatus) -> None:
    with pytest.raises(ValueError, match="failed"):
        asyncio.run(
            transition(
                None,  # type: ignore[arg-type]
                uuid.uuid4(),
                to=to,
                failure=FailureReason.JOB_DEAD_LETTERED,
            )
        )
