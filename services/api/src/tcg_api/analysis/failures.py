"""Why an analysis failed — issue #265, spec §54, §66.

A failure is a **stored fact with a closed vocabulary**. `failed` alone says
that the run stopped; it does not say whether the photographs were the problem
(the one thing the user can fix), a dependency was down, or a model raised.
Before this module, `confirm-card` and the results screen each *guessed* from
`images[].quality_status` — right for the gate refusing and wrong for every
other failure, all of which looked like a healthy run whose photographs passed.

Two rules, both from the specification:

* **Never a message.** Spec §54 and rule 3 of `jobs.py`'s docstring keep
  exception text, tracebacks and anything about an image off the row and out
  of the log. :func:`failure_reason` therefore keys on an exception's *type*
  and never reads it.
* **Never a ninth code.** Spec §66's taxonomy is closed at eight (ADR 0005).
  The §66 code an analysis carries is one of two — `image_quality_failure`
  when the gate refused, `analysis_failed` otherwise — and the *reason* is the
  second field, this vocabulary, not a code.

The vocabulary is the analyzers' pattern (#249): the strings stored here are
what the client keys its copy off, exactly. Rewording one re-keys a sentence.
`timed_out` and `stalled` are declared here and written by nobody yet — the
bounding issue (#272) writes them, so they exist before their writer for the
same reason `SessionStatus.EXPIRED` did.
"""

from __future__ import annotations

from enum import StrEnum

from tcg_domain.errors import CatalogUnavailable, InvalidConditionAssessment
from tcg_grading_companies.errors import GradingCompanyError
from tcg_shared.storage.errors import StorageError

from tcg_api.errors import ErrorCode
from tcg_api.grading.rules import GradingRulesUnavailable

__all__ = ["FailureReason", "failure_reason"]


class FailureReason(StrEnum):
    """The closed set of reasons an analysis stops — `analyses.failure_reason`."""

    #: Spec §19's gate refused a photograph. The only reason that is the
    #: user's to fix, and the only one carrying `image_quality_failure`.
    UNUSABLE_PHOTOGRAPH = "unusable_photograph"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    GRADING_RULES_UNAVAILABLE = "grading_rules_unavailable"
    IMAGE_STORE_UNAVAILABLE = "image_store_unavailable"
    #: A model *raised*. A model that declined is three recorded refusals in
    #: `grade_predictions` and never a failure (#227, ADR 0011).
    MODEL_FAILED = "model_failed"
    #: The runner gave up after its retries on something with no name here —
    #: including the analysis store itself, whose outage is the one `_fail`
    #: cannot record.
    JOB_DEAD_LETTERED = "job_dead_lettered"
    #: Reserved for #272's soft time limit. Declared, not yet written.
    TIMED_OUT = "timed_out"
    #: Reserved for #272's stall sweep. Declared, not yet written.
    STALLED = "stalled"

    @property
    def code(self) -> ErrorCode:
        """The spec §66 code this reason travels under — one of two, never a ninth."""
        if self is FailureReason.UNUSABLE_PHOTOGRAPH:
            return ErrorCode.IMAGE_QUALITY_FAILURE
        return ErrorCode.ANALYSIS_FAILED


def failure_reason(error: BaseException) -> FailureReason:
    """Which reason an exception on the job runner's path is recorded as.

    Keyed on the specific classes and never on `ConnectionError`: the catalog,
    the grading rules, the analysis store and the object store all subclass it,
    so it names none of them. Anything unnamed is `job_dead_lettered` — the
    honest bucket, not a guess.
    """
    if isinstance(error, CatalogUnavailable):
        return FailureReason.CATALOG_UNAVAILABLE
    if isinstance(error, GradingRulesUnavailable):
        return FailureReason.GRADING_RULES_UNAVAILABLE
    if isinstance(error, StorageError):
        return FailureReason.IMAGE_STORE_UNAVAILABLE
    if isinstance(error, GradingCompanyError | InvalidConditionAssessment):
        return FailureReason.MODEL_FAILED
    return FailureReason.JOB_DEAD_LETTERED
