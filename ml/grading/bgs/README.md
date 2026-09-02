# `ml/grading/bgs`

Predicts the grade BGS would issue for one assessed card, as a probability
distribution — spec §24, issue #225.

`predict(assessment)` takes M7's neutral `ConditionAssessment` (#180, #186) and
answers a `GradePrediction`: a `GradeDistribution` over **every one of BGS's
nineteen grades**, a `model_confidence`, and a `model_version`. Never a single
expected grade — the distribution is the value, and `most_likely_grade` is a
view of it.

## The half grade this package exists for

Spec §24 ends with a sentence of its own: *"BGS must support half grades."* BGS
issues nineteen grades where PSA and TAG issue eighteen, and the extra one is
**9.5** — the grade the three companies actually disagree about. The warning
this file has carried since M0 is now a tested fact rather than a caution:

> Grade keys must accommodate BGS half grades — do not assume integer grades.

A distribution from this package names 9.5, and `PSA_SCALE.validate` and
`TAG_SCALE.validate` both refuse it. That is what makes the grade scale a
per-company fact, and it is why `expected_value` prices per
`(grading_company, grade)` and `GET /cards/{id}/market` returns 55 pairs rather
than 54.

## The rule: the worst of four subgrades

Beckett prints **four subgrades** — centering, corners, edges, surface — on the
same 1-to-10 half-point scale as the overall grade, and the overall grade is the
worst of them. `packages/grading-companies` already carries the sharpest
statement of that rule in the repository, as the definition of a designation:
Black Label is *"a BGS 10 whose four subgrades are each 10"*. As arithmetic:

```text
P(BGS 10) = Π P(subgrade = 10)
```

which is what this predictor computes, and why a BGS 10 is harder to reach here
than a PSA 10 is in `ml/grading/psa`. Each category earns a subgrade in **half
grades**, that subgrade is **quantised onto the ladder** (Beckett prints 9.5,
never 9.37), and the answer is the exact distribution of their minimum.

Three properties follow, and `tests/test_grade_predictors_differ.py` at the
repository root asserts each against both siblings rather than assuming it:

| | PSA | TAG | BGS |
| --- | --- | --- | --- |
| unit | ladder steps | points out of 1000 | **half grades** |
| across categories | weighted sum | weighted mean | **minimum** |
| onto the ladder | continuous centre | non-uniform bands | **quantised subgrade** |

So wrecking a *second* category costs both siblings again and costs BGS almost
nothing, and BGS's answer plateaus as damage grows continuously where both
siblings slide. Neither is reachable by retuning either of them — which is spec
§2.2's point, tested rather than restated.

**Damage accumulates within a category and is minimised across them.** That
asymmetry is BGS's rule: a second chipped corner makes the corner subgrade
worse, because Beckett prints one number for all four corners.

## What v0.1.0 claims, and what it deliberately never does

It is a **declared-uncertainty baseline**, on the basis
[ADR 0011](../../../docs/adr/0011-the-v1-grade-predictor-basis.md) fixed.
Nothing is trained and nothing is fitted: `grading_outcomes` holds zero rows,
and Beckett's published standard is that company's text, which this repository
does not reproduce — `companies.py` additionally records the BGS scale as the
one entry in that package not read from the company's own page.

**A thin assessment widens the distribution; it never refuses.** There is no
coverage gate and no confidence gate. The blend toward a declared ignorance
subgrade is **per category**, because the minimum consumes the subgrades one at
a time; an assessment with every category refused answers mid-ladder, not
"probably a Pristine 10". `model_confidence` is bounded above both by the
assessment's own confidence and by a declared per-version ceiling.

**No bucket.** §24's `7_or_lower` stays legal everywhere it is legal today; this
predictor spreads that mass over the tail grades instead.

**No subgrade is emitted, and no designation.** The four subgrades are this
rule's internal working values, surfaced by nothing — no route, no record, no
field. Predicting four more distributions is a second product decision with its
own evaluation burden. Black Label is a label *on* grade 10, not a value on the
scale, and V1 does not authenticate; the predictor answers `10`.

`manufacturing_defects` and `eye_appeal` are read by nothing here — the first is
derived from surface and edges and would be counted twice, the second is refused
by construction.

## Versioning

The V1 implementation is a heuristic versioned by `GRADING_BGS_VERSION` — a code
constant, never a `model_bundles` row, because a row names a trained artifact
with a dataset version and metrics. Changing any threshold bumps it.

```text
grading-bgs-heuristic-v0.1.0   this baseline
grading-bgs-v0.2.0             the trained bundle that replaces it
```

Never `/latest/`. The first trained model arrives through the same injected
predictor seam and supersedes this constant with a registry version; nothing
here has to be undone for it.
