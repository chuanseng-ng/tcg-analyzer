"""The grading-company port — what the application may ask of a grader.

Spec §22 names five responsibilities and three implementations. This module is
the first; :mod:`tcg_grading_companies.companies` is the second. The point of
the split is CLAUDE.md's "external providers are replaceable" invariant:
**adding CGC or ARS later must require only a new adapter, and no change to any
caller.**

Two shape decisions worth reading before changing anything here.

*Every method is synchronous.* :class:`tcg_domain.repository.CardRepository` and
:class:`tcg_shared.storage.port.ObjectStorage` are `async` because they reach a
database and an object store, and a blocking call on the API's event loop is an
outage under load. This port reaches nothing: four of its methods return
in-package constants, and M8's ``predict_grade`` runs a model in-process inside
a Celery task, which is not the API's event loop and is exactly where blocking
belongs.

*``predict_grade`` is declared in full and refuses.* It exists from the
beginning so that M8 fills an existing contract rather than reshaping the
interface after callers have been written against a narrower one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from tcg_domain.confidence import Confidence, Uncertain
from tcg_domain.distribution import GradeDistribution

from tcg_grading_companies.reference import GradingRules, ServiceOption
from tcg_grading_companies.scale import GradeScale

__all__ = ["GradePrediction", "GradingCompany", "GradingCompanyAdapter"]


class GradingCompany(StrEnum):
    """The companies V1 ships — a vocabulary, not the set of valid companies.

    Members are `str`, exactly as :class:`tcg_domain.card.Game`'s are, and
    every field holding a company is typed `str`. That is deliberate and it is
    the difference between this and :class:`tcg_domain.analysis.AnalysisStatus`,
    which *is* closed: a state nobody wrote code for is a row no transition can
    leave, whereas a grading company nobody wrote an adapter for is still a
    grading company.

    It matters here more than anywhere else in the codebase. Spec §22's
    requirement is that a fourth company costs one new adapter and no caller
    change — which stops being true the moment a closed enum sits between the
    caller and the adapter, because then it also costs an edit here and a
    migration of every CHECK constraint built from it.

    CGC and ARS are out of V1 scope by decision, not by representation.
    """

    PSA = "psa"
    TAG = "tag"
    BGS = "bgs"


@dataclass(frozen=True, slots=True)
class GradePrediction:
    """What a grading model concludes — spec §24's three outputs, verbatim.

    Args:
        grade_probability: The full distribution. Never a single expected
            grade: CLAUDE.md's central invariant is that the distribution *is*
            the value, and :attr:`~tcg_domain.distribution.GradeDistribution.most_likely_grade`
            is a view of it.
        model_confidence: How much the model trusts its own distribution —
            distinct from the distribution's own spread, which can be narrow
            and wrong.
        model_version: The exact bundle that produced it, such as
            ``grading-psa-v0.2.0``. Never ``latest``.
    """

    grade_probability: GradeDistribution
    model_confidence: Confidence
    model_version: str


class GradingCompanyAdapter(Protocol):
    """One grading company, as the rest of the application sees it.

    Implementations must raise only :mod:`tcg_grading_companies.errors` types,
    so swapping one for another changes no caller's error handling.
    """

    @property
    def company(self) -> str:
        """The company's lowercase slug — see :class:`GradingCompany`."""

    def get_grade_scale(self) -> GradeScale:
        """Every grade this company can issue.

        The three V1 companies do not agree: PSA and TAG issue no 9.5 and BGS
        does. Code that assumes a shared scale — or integer grades — is wrong
        for all three.
        """

    def get_rules(self) -> GradingRules:
        """The version of this company's published standard the product is on.

        Spec §57 records this identifier against every analysis, so an analysis
        run before a company revised its standard stays interpretable
        afterwards.
        """

    def get_supported_card_types(self) -> tuple[str, ...]:
        """The games this company grades, as :class:`tcg_domain.card.Game`
        slugs.

        Games only. Language, era and set restrictions are real — a company may
        decline a set it has no population data for — and none of them is
        modelled in V1, because nothing in the product asks. A caller must not
        read an answer here as "this company will accept this particular card".
        """

    def get_service_options(self) -> tuple[ServiceOption, ...]:
        """The submission tiers this company offers.

        Empty for every V1 adapter. Fees are configurable economic inputs
        (spec §45) and belong to M5's economic configuration — see
        :mod:`tcg_grading_companies.reference`.
        """

    def predict_grade(self, condition: object) -> Uncertain[GradePrediction]:
        """Predict a grade distribution from a neutral condition representation.

        Args:
            condition: The neutral, company-independent condition
                representation. M7 defines that type and M8 narrows this
                parameter to it; inventing a placeholder here would be a second
                definition for M7 to reconcile against its own.
                ``# ponytail: typed `object` until M7 exists; narrow it there.``

        Returns:
            A :class:`GradePrediction`, or
            :data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION` when the
            condition representation does not support one. Spec §2.7 makes that
            a legitimate answer, not a failure.

        Raises:
            GradePredictionUnavailable: In V1, always. Spec §24's models arrive
                in M8; until then a fabricated distribution would be exactly
                the confidently-wrong output the product exists to avoid.
        """
