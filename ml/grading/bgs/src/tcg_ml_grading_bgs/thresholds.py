"""The numbers the BGS grade prediction runs on — spec §24, issue #225.

`ml/grading/psa/thresholds.py` is the structural model — a frozen dataclass a
caller may replace wholesale, and :meth:`BGSGradingThresholds.as_record` so a
stored prediction explains itself — and, as with TAG, **the numbers are not**.
They are denominated in a third currency, because BGS grades in a third way.

    PSA   ladder steps of an eighteen-point ladder — a human eye's ranking of
          the axes, and a fully damaged one costs so many positions.
    TAG   points out of 1000 — a machine's own scale, mapped onto the ladder
          through a band table.
    BGS   **half grades**, which is what Beckett prints. Four subgrades on the
          same 1-to-10 scale as the overall grade, in 0.5 increments, and the
          overall grade is the worst of them.

BGS's ladder is also the only V1 ladder that is *uniform in grade value*: PSA's
and TAG's step from 9 straight to 10, BGS's steps by a half point all the way
(`companies.py` records why). So half-grade arithmetic is meaningful here and
would be wrong for either sibling, and one position on this ladder is exactly
one half grade — which is what lets a subgrade be **quantised onto the ladder**
before it is compared, the way a printed subgrade is.

There are deliberately **no category weights** in this file. A minimum has none:
the worst subgrade decides, and asking which category's subgrade matters more
would be asking a weighted mean, which is TAG's aggregation retuned.

**Beckett's published standard is not reproduced here and not reconstructed**,
for the same reason `reference.py` sets ``rules = EMPTY_RULES`` on every record —
and `companies.py` flags the BGS entry as the weakest evidence in the package,
read from a search index rather than from the company's own page. Every number
below is this project's own **declared prior, not a fitted parameter** — ADR
0011's whole point rather than an apology for it. `grading_outcomes` holds zero
rows. The spread these constants produce is the model's declaration of how
little it knows.

**Changing a value means bumping the version**, exactly as it does for an
analyzer. :data:`GRADING_BGS_VERSION` names a fixed set of numbers the way a
model bundle names fixed weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

__all__ = [
    "DEFAULT_BGS_GRADING_THRESHOLDS",
    "GRADING_BGS_VERSION",
    "BGSGradingThresholds",
]

#: What predicted a BGS grade. Recorded beside every prediction this package
#: produced; never a pointer to "current", per the project's versioning
#: invariant. ADR 0011 decision 6: a baseline's version is a code constant,
#: never a `model_bundles` row — a row names a trained artifact with a dataset
#: version and metrics.
#:
#: The `heuristic` infix is `grading-psa-heuristic-v0.1.0`'s, and it is
#: load-bearing for the same reason: spec §59's grammar reserves
#: `grading-bgs-v0.2.0` for the *trained* bundle that replaces this one, and the
#: two must not collide in `analyses.model_bundle_version`.
GRADING_BGS_VERSION: Final = "grading-bgs-heuristic-v0.1.0"


@dataclass(frozen=True, slots=True)
class BGSGradingThresholds:
    """What each category's subgrade loses, and how sure any one subgrade is.

    Every value denominated in **half grades** is a number of positions on
    BGS's own nineteen-point ladder, because on that ladder one position is one
    half grade.

    Raises:
        ValueError: If the penalty chain is not ordered, a spread is not
            positive, or a fraction is out of range.
    """

    #: What one finding of each severity takes off its **own category's**
    #: subgrade, in half grades. `DefectSeverity` is an ordinal by decision
    #: (#158), and turning it into a number is a modelling choice made where the
    #: model lives.
    #:
    #: Damage **accumulates within** a category and the categories are then
    #: **minimised across** — that asymmetry is BGS's rule rather than an
    #: inconsistency. Beckett prints a subgrade per category, so a second
    #: chipped corner makes the corner subgrade worse; the overall grade is
    #: then the worst subgrade, so a second wrecked *category* does not make the
    #: card worse than its worst one already did.
    minor_penalty: float = 0.75
    moderate_penalty: float = 1.75
    severe_penalty: float = 3.0

    #: What a fully off-centre card loses from its centering subgrade, in half
    #: grades. Enough to reach the bottom half of the ladder on its own — BGS is
    #: the company whose centering tolerances are the strictest of the three and
    #: the reason the hobby talks about BGS centering at all — but not the whole
    #: ladder: a badly centred card is still a card.
    centering_full_penalty: float = 12.0

    #: The border offset at which centering is penalised in full. A ratio is
    #: ``near / (near + far)`` with 0.5 perfect (`ml/centering`), so the offset
    #: is ``abs(ratio - 0.5) * 2`` and 0.40 is roughly a 70/30 split.
    #:
    #: Front and back are weighted equally, as at TAG and unlike PSA, but for a
    #: different reason: BGS prints **one** centering subgrade, so the two faces
    #: are already one number by the time the rule sees them.
    centering_tolerance_offset: float = 0.40

    #: Where a category nobody could read sits, **as a subgrade** — a grade
    #: value on BGS's own scale, not a position. Each category's subgrade is
    #: blended toward it in proportion to the share of that category no analyzer
    #: covered, so that ignorance never reads as evidence of a flawless card: an
    #: unread category loses nothing, so an unblended subgrade would sit at 10
    #: and the answer would be *"probably a Pristine 10"* — #91's "not measured
    #: is never 0%" committed in the optimistic direction.
    #:
    #: The blend is **per category**, unlike either sibling's, because the
    #: minimum consumes the subgrades one at a time: ignorance about the corners
    #: must not widen the centering subgrade.
    #:
    #: 7.0 rather than the middle of the scale, and the difference is the rule
    #: speaking. The minimum of four uncertain readings sits below any one of
    #: them, so four unread subgrades declared at 7 produce an overall answer
    #: peaking at 5.5 — mid-ladder, which is the only honest position at zero
    #: information. `unmeasured_score = 620` at TAG was chosen the same way: for
    #: where the *answer* lands, never for where the input sits.
    unmeasured_subgrade: float = 7.0

    #: How sure a **fully covered** subgrade is, in half grades. It is not zero
    #: and must not become zero: an uncalibrated declared mapping is not
    #: entitled to a narrow answer even when every category was read.
    #:
    #: 0.6 is load-bearing in a way the siblings' spreads are not, because the
    #: minimum compounds it: ``P(BGS 10) = Π P(subgrade = 10)`` over the four,
    #: which is `companies.py`'s Black Label — *"a BGS 10 whose four subgrades
    #: are each 10"* — as arithmetic. At 0.6 a flawlessly read card answers 9.5
    #: with 0.41 on 10; at 0.45 it answers 10 with 0.72, which is more certainty
    #: than a declared mapping has earned about anything.
    base_sigma: float = 0.6

    #: How much wider a subgrade gets at total ignorance of its category, added
    #: in proportion to the uncovered share. ADR 0011 decision 1's "a thin
    #: assessment widens", expressed as a number and applied per category.
    unmeasured_sigma: float = 3.0

    #: The ceiling on
    #: :attr:`~tcg_grading_companies.port.GradePrediction.model_confidence` —
    #: ADR 0011 decision 1's second bound, beside the assessment's own
    #: confidence. It shares PSA's and TAG's value because it is the ADR's rule
    #: about what an uncalibrated mapping may claim, not a property of BGS.
    confidence_ceiling: float = 0.35

    def __post_init__(self) -> None:
        for name in ("centering_full_penalty", "base_sigma"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")
        if self.unmeasured_sigma < 0:
            raise ValueError(
                f"unmeasured_sigma must not be negative, got {self.unmeasured_sigma!r}"
            )
        for name in ("centering_tolerance_offset", "confidence_ceiling"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must lie in (0, 1], got {value!r}")
        if not 0.0 < self.minor_penalty < self.moderate_penalty < self.severe_penalty:
            raise ValueError(
                "the severity penalties must be an ordered chain of positive numbers, got "
                f"{self.minor_penalty!r}, {self.moderate_penalty!r} and "
                f"{self.severe_penalty!r}"
            )

    def as_record(self) -> dict[str, float]:
        """The form merged into a stored record beside the prediction.

        Prefixed, so it cannot collide with the four analyzers' records or with
        `grading_psa_`'s and `grading_tag_`'s in the same stored document.
        """
        return {f"grading_bgs_{name}": float(value) for name, value in asdict(self).items()}


#: The values this project ships. :data:`GRADING_BGS_VERSION` names them.
DEFAULT_BGS_GRADING_THRESHOLDS: Final = BGSGradingThresholds()
