"""The grading companies' HTTP surface — spec §64's `GET /grading-companies`.

One endpoint, and its purpose is a requirement stated negatively: **the frontend
must never hard-code a grade scale.** PSA and TAG issue eighteen grades and no
9.5; BGS issues nineteen and has one. A comparison UI built on a single shared
scale silently misrenders one company, and a fourth company added post-V1 must
cost one new adapter here and no frontend change at all (spec §22).

It also reports which version of each company's published standard is in force,
so a result can be tied back to it — spec §23's versioned rules, which §57
records against every analysis as `grading_rules_version`.

**The scale and the version come from different places, deliberately.** The
grades are `tcg_grading_companies`' in-package constants; the version is read
from the `grading_rules` table, because that is what an analysis resolves
against and therefore the only answer that cannot quietly disagree with a
recorded result. The two agree today because `tcg-seed-grading-rules` writes the
table *from* those constants. A successor row published by hand would leave the
scale here stale — so bump the adapter in the same commit as the row.

**No fees.** Spec §45 makes grading cost a configurable economic input, so it
belongs to M5's economic configuration where a user can change it, not to a
table here that would go stale quarterly and disagree with whatever the engine
was actually told. All three adapters' `get_service_options()` return empty for
that reason (#46), so an always-empty array carrying a `Money` schema nothing
fills would be a contract for data that does not exist.

The router holds HTTP and nothing else. The SQL lives in `tcg_api.grading.rules`,
exactly as `routers/catalog.py` delegates to `tcg_api.catalog.versions`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from tcg_grading_companies import ADAPTERS, GradingCompanyAdapter, GradingRules

from tcg_api.database import get_session_factory
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.grading.rules import GradingRulesUnavailable, rules_in_force

__all__ = [
    "GradingCompaniesResponse",
    "GradingCompanyResponse",
    "GradingRulesResponse",
    "grading_rules_in_force",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["grading"])

_UNREACHABLE = "The grading rules could not be read."

#: Published standards change at the pace a grading company revises them, which
#: is years. An hour is short enough that a newly seeded version appears the same
#: working day and long enough that a client rendering a picker does not ask
#: again on every visit. `public` because the response carries nothing about the
#: caller — no cookie is read and every caller gets the same three companies.
#:
#: The first `Cache-Control` this service sends. A 503 gets none: `ApiError`
#: builds its own response, so an unreachable database is never cached.
_CACHE_CONTROL: Final = "public, max-age=3600"


class GradingRulesResponse(BaseModel):
    """The version of one company's published standard currently in force.

    Spec §23's record, minus the `rules` body — that is empty in V1 by decision
    (#46: the published standards are the companies' copyrighted text, and what
    §57 needs is the identifier plus a source a human can open).
    """

    version: str = Field(
        description=(
            "The identifier an analysis retains — spec §57's `grading_rules_version`. "
            "No grading company publishes a version for its standard, so this one is "
            "this repository's, stamped with the date the standard was read."
        ),
        examples=["psa-rules-2026-08-24"],
    )
    effective_from: date | None = Field(
        description=(
            "When the company's published standard took effect, where the company "
            "states one. `null` where it states none — never a guess."
        ),
        examples=["2008-02-01"],
    )
    effective_to: date | None = Field(
        description=(
            "When it stopped applying, or `null` while it is current. Derived from "
            "the next version's start rather than stored, so two versions of one "
            "company cannot overlap."
        ),
        examples=[None],
    )
    source: str = Field(
        description="Where the standard was read. A URL a human can open.",
        examples=["https://www.psacard.com/gradingstandards"],
    )
    verified_on: date = Field(
        description="When the source was last read.",
        examples=["2026-08-24"],
    )


class GradingCompanyResponse(BaseModel):
    """One grading company, as a client needs to render it."""

    company: str = Field(
        description="The company's lowercase slug. The key a graded price is stored under.",
        examples=["psa"],
    )
    display_name: str = Field(
        description="What to show a user.",
        examples=["PSA"],
    )
    grades: list[str] = Field(
        description=(
            "Every grade this company can issue, ascending. Not shared between "
            "companies: PSA and TAG have no 9.5 and BGS does. Render from this list "
            "rather than from a hard-coded scale."
        ),
        examples=[["1", "1.5", "2", "8.5", "9", "10"]],
    )
    rules: GradingRulesResponse | None = Field(
        description=(
            "The published standard in force today, or `null` when no version of "
            "this company's standard has been recorded."
        ),
    )


class GradingCompaniesResponse(BaseModel):
    """The body of a successful `GET /grading-companies`."""

    companies: list[GradingCompanyResponse] = Field(
        description=(
            "Every company the product supports, in a stable order. A company added "
            "post-V1 appends to it."
        ),
    )


async def grading_rules_in_force() -> Mapping[str, GradingRules | None]:
    """Resolve every supported company's standard as of today, or answer 503.

    A dependency rather than a step inside the route, so a test can override the
    whole resolution with `dependency_overrides` and needs no database. That is
    the same seam `routers/catalog.py` gets from its repository — but there the
    port supplies it, and `tcg_api.grading.rules` is deliberately plain module
    functions (no interface with one implementation), so the dependency has to be
    the seam itself.

    The date is this process's, not the database's. `analysis/sessions.py`
    compares expiry against the database's `now()` because a skewed application
    clock would otherwise extend a session; nothing here grants anything, and a
    day's skew on reference data that changes every few years is not a
    correctness question.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("grading rules session factory could not be built", exc_info=True)
        raise _unreachable() from error

    today = datetime.now(UTC).date()
    async with factory() as session:
        try:
            # ponytail: one statement per company against a three-row table. One
            # windowed statement if the company list ever stops being three.
            return {company: await rules_in_force(session, company, today) for company in ADAPTERS}
        except GradingRulesUnavailable as error:
            logger.warning("grading rules could not be read", exc_info=True)
            raise _unreachable() from error


def _unreachable() -> ApiError:
    """503 `provider_error`, distinguished from the catalog's and the store's.

    Spec §66's taxonomy has eight codes and no `not_found`; a deployment that
    cannot read its own reference data is unavailable, not mistaken, so 503 is
    the honest status. `details.reason` is the fifth this service uses.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "grading_rules_unreachable"},
    )


def _company(
    slug: str, adapter: GradingCompanyAdapter, rules: GradingRules | None
) -> GradingCompanyResponse:
    return GradingCompanyResponse(
        company=slug,
        # ponytail: every grading company's display name is its slug uppercased —
        # PSA, TAG, BGS, and CGC, SGC and ARS post-V1. If one ever is not, that is
        # a `display_name` on the adapter, where the rest of its published
        # reference data lives, never a lookup table here.
        display_name=slug.upper(),
        grades=[str(grade) for grade in adapter.get_grade_scale().ordered],
        rules=None if rules is None else _rules(rules),
    )


def _rules(rules: GradingRules) -> GradingRulesResponse:
    return GradingRulesResponse(
        version=rules.version,
        effective_from=rules.effective_from,
        effective_to=rules.effective_to,
        source=rules.source,
        verified_on=rules.verified_on,
    )


@router.get(
    "/grading-companies",
    response_model=GradingCompaniesResponse,
    summary="List the grading companies and their grade scales",
    description=(
        "Spec §64's grading endpoint. Returns every supported company with the exact "
        "grades it can issue and the version of its published standard in force today "
        "(spec §23), so a result can be tied back to it. **Render the scale from "
        "`grades` rather than hard-coding one**: PSA and TAG issue no 9.5 and BGS "
        "does, so a shared scale misrenders one of them, and a company added post-V1 "
        "appears here with no frontend change. Slow-moving reference data — the "
        "response carries `Cache-Control: public, max-age=3600`. No fees: spec §45's "
        "grading costs are user-configured economic inputs, not fetched here."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": (
                "The grading rules could not be read. `details.reason` is "
                "`grading_rules_unreachable`."
            ),
        },
    },
)
async def list_grading_companies(
    response: Response,
    rules: Annotated[Mapping[str, GradingRules | None], Depends(grading_rules_in_force)],
) -> GradingCompaniesResponse:
    """List every supported grading company, its scale and its rules version."""
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return GradingCompaniesResponse(
        companies=[_company(slug, adapter, rules.get(slug)) for slug, adapter in ADAPTERS.items()]
    )
