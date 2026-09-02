"""The PSA, TAG and BGS adapters, and the reference data behind them.

Everything below is a published fact about a grading company, read from that
company's own site and dated. Nothing is inferred, and nothing is shared
between companies that the companies do not share.

The one thing worth knowing before reading the scales
-----------------------------------------------------
All three companies issue half grades, and **the grade they disagree about is
9.5**: PSA and TAG have none, BGS does. That is the opposite of the common
summary — "BGS has half grades, PSA and TAG don't" — which is wrong for sixteen
of PSA's eighteen grades. Code written to it would silently refuse a PSA 8.5,
one of the more common grades in the hobby.

    PSA — "PSA will add a half-point grade within each of the 1-10 numbers with
    the exception of a 9.5 grade. We felt it was unnecessary to add a third
    'Mint' grade since PSA already had a Mint 9 and Gem Mint 10 grade."
    https://www.psacard.com/articles/articleview/5212 — read in full 2026-08-24

    TAG — its scale table lists nineteen rows for eighteen grades: 1 through 9
    with a half point at every level, then 10 twice, as Gem Mint (score
    950-989) and Pristine (990-1000). There is no 9.5 row.
    https://taggrading.com/pages/scale — read in full 2026-08-24

    BGS — "1 (Poor) to 10 (Pristine), in 0.5 increments"; the floor is 1, so
    there is no BGS 0.5.
    https://www.beckett.com/grading/scale

**The BGS entry is the weakest evidence here, and it is marked rather than
levelled up.** beckett.com sits behind CloudFront and refused both an automated
fetch and a real browser on 2026-08-24 with a 403, so unlike the other two this
scale was not read from the page itself — it rests on a search index of that
page. The claim is uncontroversial and consistent everywhere it appears, and it
is still second-hand. Confirm it by hand from a machine Beckett will serve
before anything downstream treats a BGS price as authoritative.

Designations, which are not grades
----------------------------------
Five designations exist and are **not** grades on a scale:

* **PSA "Authentic" and "Authentic Altered"** — issued in place of a numeric
  grade. V1 does not authenticate cards at all, so the product has nothing to
  say about a card that receives one — but it must still be able to *record*
  that one was issued.
* **BGS Black Label** — a BGS 10 whose four subgrades are each 10. A label on
  grade 10.
* **TAG Pristine 10 and Gem Mint 10** — two designations for grade 10.

Each would have to widen :class:`tcg_domain.grade.Grade` beyond "a `Decimal`
multiple of 0.5 in [0, 10]", which is the property that makes a grade usable as
a distribution key and a database key at all. So :class:`Designation` names them
as their own vocabulary, and :data:`DESIGNATIONS` says which company issues
which — because a designation is as company-specific as a scale is, and PSA
cannot issue a Black Label.

**The prediction side does not read them and must not start.** Spec §24's
output is a distribution over grades; a designation is something a slab already
carries. The one consumer today is #165's ``grading_outcomes``, which records
what one company actually issued, once.

``ponytail: designations are not grades. #165 took the designation column beside
the grade in `grading_outcomes`, which is what this note anticipated. If M4's
market data ever prices a Black Label separately from an ordinary BGS 10, that
is a second such column in `market_observations` — never a new value on the
scale.``

Versions
--------
**No grading company publishes a version for its grading standard.** The
identifiers below are therefore this repository's, stamped with the date the
standard was read — the same answer ADR 0006 reached for §36's ``data_version``.
Re-reading and finding a change publishes a new dated version; an old one is
never edited, per spec §23.

Effective dates are a different matter and are recorded where a company states
one. PSA does: "Starting February 1, 2008, all cards submitted to PSA will be
graded utilizing this new scale." TAG and BGS state none, and that is written
down as `None` rather than guessed at from a copyright footer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final

from tcg_domain.card import POKEMON
from tcg_domain.condition import ConditionAssessment
from tcg_domain.confidence import Uncertain
from tcg_domain.grade import MAX_GRADE, Grade

from tcg_grading_companies.errors import GradePredictionUnavailable
from tcg_grading_companies.port import GradePrediction, GradingCompany, GradingCompanyAdapter
from tcg_grading_companies.reference import GradingRules, ServiceOption
from tcg_grading_companies.scale import GradeScale

__all__ = [
    "ADAPTERS",
    "BGS_RULES",
    "BGS_SCALE",
    "DESIGNATIONS",
    "PSA_RULES",
    "PSA_SCALE",
    "TAG_RULES",
    "TAG_SCALE",
    "BGSAdapter",
    "Designation",
    "PSAAdapter",
    "TAGAdapter",
]

#: When all three standards were last read. ADR 0006's ninety-day rule applies:
#: re-read before relying on any of this after 2026-11-22.
_VERIFIED_ON: Final = date(2026, 8, 24)

#: The games V1's three companies grade. Games only — see
#: `GradingCompanyAdapter.get_supported_card_types` for what this does not say.
#: `str(...)`, not the `Game` member: the domain coerces its slugs to plain
#: `str` so that a member never survives into a repr, a log line or a
#: serialised payload as `Game.POKEMON`. Same rule here.
_SUPPORTED_CARD_TYPES: Final[tuple[str, ...]] = (str(POKEMON),)

_HALF: Final = Decimal("0.5")


def _half_steps(low: str, high: str) -> frozenset[Grade]:
    """Every grade from `low` to `high` inclusive, in half-point increments."""
    start, stop = Decimal(low), Decimal(high)
    steps = int((stop - start) / _HALF)
    return frozenset(Grade(start + _HALF * step) for step in range(steps + 1))


# --------------------------------------------------------------------------
# PSA
# --------------------------------------------------------------------------
#: Eighteen grades: the half-point run stops at 9, and 10 joins on its own.
#: That expression *is* PSA's rule — "a half-point grade within each of the
#: 1-10 numbers with the exception of a 9.5" — rather than a list to keep in
#: step with one.
PSA_SCALE: Final = GradeScale(
    company=str(GradingCompany.PSA),
    version="psa-rules-2026-08-24",
    grades=_half_steps("1", "9") | {Grade(MAX_GRADE)},
)

PSA_RULES: Final = GradingRules(
    company=str(GradingCompany.PSA),
    version=PSA_SCALE.version,
    # "Starting February 1, 2008, all cards submitted to PSA will be graded
    # utilizing this new scale" — the half-point scale above.
    effective_from=date(2008, 2, 1),
    source="https://www.psacard.com/gradingstandards",
    verified_on=_VERIFIED_ON,
)

# --------------------------------------------------------------------------
# TAG
# --------------------------------------------------------------------------
#: Eighteen grades, the same shape as PSA's and for the same stated reason —
#: half points at every level "except for between 9 and 10". TAG additionally
#: scores on a 1-to-1000 point scale that it maps onto these; the mapping is TAG's
#: and the product has no use for the raw score.
TAG_SCALE: Final = GradeScale(
    company=str(GradingCompany.TAG),
    version="tag-rules-2026-08-24",
    grades=_half_steps("1", "9") | {Grade(MAX_GRADE)},
)

TAG_RULES: Final = GradingRules(
    company=str(GradingCompany.TAG),
    version=TAG_SCALE.version,
    # TAG states none.
    effective_from=None,
    source="https://taggrading.com/pages/scale",
    verified_on=_VERIFIED_ON,
)

# --------------------------------------------------------------------------
# BGS
# --------------------------------------------------------------------------
#: Nineteen grades — the only V1 scale carrying 9.5. The floor is 1, not 0.5:
#: "0.5 increments" describes the step, not the lowest grade.
#:
#: BGS also prints four subgrades (centering, corners, edges, surface) on the
#: label. They are not modelled: the product predicts the grade a card would
#: receive, and a subgrade is a component of Beckett's own reasoning, not a
#: separate thing to price.
BGS_SCALE: Final = GradeScale(
    company=str(GradingCompany.BGS),
    version="bgs-rules-2026-08-24",
    grades=_half_steps("1", "10"),
)

BGS_RULES: Final = GradingRules(
    company=str(GradingCompany.BGS),
    version=BGS_SCALE.version,
    # Beckett states none — and see the module docstring: this record is the
    # one here not read from the company's own page.
    effective_from=None,
    source="https://www.beckett.com/grading/scale",
    verified_on=_VERIFIED_ON,
)


# --------------------------------------------------------------------------
# The adapters
# --------------------------------------------------------------------------
class _ReferenceAdapter:
    """A V1 adapter: published reference data, and one honest refusal.

    All three companies share this because in M4 they differ only in their
    data. When M8 gives each its own model, ``predict_grade`` moves down into
    the three subclasses and this base keeps the four getters.
    """

    company: ClassVar[str]
    _scale: ClassVar[GradeScale]
    _rules: ClassVar[GradingRules]

    def get_grade_scale(self) -> GradeScale:
        return self._scale

    def get_rules(self) -> GradingRules:
        return self._rules

    def get_supported_card_types(self) -> tuple[str, ...]:
        return _SUPPORTED_CARD_TYPES

    def get_service_options(self) -> tuple[ServiceOption, ...]:
        """Empty in V1 — fees are M5's configurable economic inputs (spec §45).

        Not a gap: a fee table here would be a second place for numbers a user
        can already override to drift, in a currency the product does not
        report in.
        """
        return ()

    def predict_grade(
        self,
        condition: ConditionAssessment,  # noqa: ARG002
    ) -> Uncertain[GradePrediction]:
        raise GradePredictionUnavailable(
            f"no {self.company} grading model exists yet: spec §24's per-company models "
            "arrive in M8. Returning a distribution here would be fabricated certainty."
        )


class PSAAdapter(_ReferenceAdapter):
    """Professional Sports Authenticator."""

    company = str(GradingCompany.PSA)
    _scale = PSA_SCALE
    _rules = PSA_RULES


class TAGAdapter(_ReferenceAdapter):
    """Technical Authentication & Grading."""

    company = str(GradingCompany.TAG)
    _scale = TAG_SCALE
    _rules = TAG_RULES


class BGSAdapter(_ReferenceAdapter):
    """Beckett Grading Services."""

    company = str(GradingCompany.BGS)
    _scale = BGS_SCALE
    _rules = BGS_RULES


#: Every company the product supports, by slug. The annotation is the
#: conformance check: mypy proves each adapter satisfies the Protocol here, so
#: there is no separate conformance test — the same trick `card_repository()`'s
#: `yield` uses in `services/api`.
#:
#: A fourth company is a new adapter and one line here. Nothing else.
ADAPTERS: Final[Mapping[str, GradingCompanyAdapter]] = MappingProxyType(
    {
        str(GradingCompany.PSA): PSAAdapter(),
        str(GradingCompany.TAG): TAGAdapter(),
        str(GradingCompany.BGS): BGSAdapter(),
    }
)


class Designation(StrEnum):
    """A label a company issues that is not a point on its grade scale.

    A `StrEnum` for the same reason `GradingCompany` is one: the value travels
    as a plain string into a database column and out of a log line, and never
    as ``Designation.AUTHENTIC``.

    Unlike :class:`~tcg_grading_companies.port.GradingCompany` this **is** a
    closed set, and the difference is not an inconsistency. A fourth company
    must cost one adapter and no caller change, so its slug cannot be gated
    here. A designation is a published label with a printed spelling: a sixth
    one is a change to what these three companies issue, which is a re-read of
    the standard and a new dated version either way.
    """

    #: PSA, issued **in place of** a numeric grade.
    AUTHENTIC = "authentic"
    #: PSA, likewise in place of a grade.
    AUTHENTIC_ALTERED = "authentic_altered"
    #: BGS, a label *on* grade 10 — the four subgrades are each 10.
    BLACK_LABEL = "black_label"
    #: TAG, on grade 10.
    PRISTINE_10 = "pristine_10"
    #: TAG, on grade 10.
    GEM_MINT_10 = "gem_mint_10"


#: Which company issues which designation, keyed by slug exactly as
#: :data:`ADAPTERS` is. Beside it rather than on the adapter Protocol: a fourth
#: company is one adapter and one line, and adding a sixth method to the port
#: would make it two.
#:
#: A company with no entry is **accepted**, not refused, wherever this is read —
#: the same rule `validated_grade_key` applies to a company with no adapter, and
#: for the same reason: `GradingCompany` is a vocabulary, and making this
#: mapping the closed set of valid companies would undo spec §22.
DESIGNATIONS: Final[Mapping[str, frozenset[Designation]]] = MappingProxyType(
    {
        str(GradingCompany.PSA): frozenset({Designation.AUTHENTIC, Designation.AUTHENTIC_ALTERED}),
        str(GradingCompany.TAG): frozenset({Designation.PRISTINE_10, Designation.GEM_MINT_10}),
        str(GradingCompany.BGS): frozenset({Designation.BLACK_LABEL}),
    }
)
