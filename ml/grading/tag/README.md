# `ml/grading/tag`

Predicts the grade TAG would issue for one assessed card, as a probability
distribution — spec §24, issue #224.

`predict(assessment)` takes M7's neutral `ConditionAssessment` (#180, #186) and
answers a `GradePrediction`: a `GradeDistribution` over **every one of TAG's
eighteen grades**, a `model_confidence`, and a `model_version`. Never a single
expected grade — the distribution is the value, and `most_likely_grade` is a view
of it.

## How this differs from `ml/grading/psa`, and why it has to

TAG's ladder is **index-for-index identical to PSA's** — half steps from 1 to 9,
then 10, and no 9.5 on either. So a TAG predictor written as PSA's rule with
different weights would be numerically PSA-shaped for every input: the single
universal `condition_score → grade` mapping CLAUDE.md's master architectural rule
forbids, shipped under a second version string. **The difference is in the rule,
not in the constants.**

The companies genuinely grade differently, and `packages/grading-companies`
already records how — read from `https://taggrading.com/pages/scale` on
2026-08-24. TAG is a machine grader that scores on a **1-to-1000 point scale**
and maps that score onto its eighteen grades through a published table whose rows
are **not evenly spaced**. PSA is a human eye ranking axes against one another.

```text
PSA    centre   = top - sum(weight * damage)          in LADDER STEPS, linear
       P(g[i]) ~= exp(-(i - centre)^2 / (2 * sigma^2))
       weights: corners 5, surface 4, centering 3, edges 3

TAG    subscore = clamp(1000 - sum(deductions), 0, 1000)   a category SUB-SCORE
       score    = sum(weight * subscore) / sum(weight)
       edge[k]  = 1000 * (1 - (1 - k/18) ** curvature)     a NON-UNIFORM band table
       P(g)     = [Phi((hi - score)/sigma) - Phi((lo - score)/sigma)] / Z
       weights: all equal — one instrument, one scale
```

Two consequences follow, and both are **asserted rather than assumed**, in
`tests/test_grade_predictors_differ.py` at the repository root:

1. **PSA distinguishes a wrecked corner from a wrecked edge; TAG does not.** PSA
   ranks its axes because an eye does. TAG scans four categories with one
   instrument and this project holds no measurement that would justify preferring
   one, so swapping which axis is wrecked leaves TAG's answer identical.
2. **PSA walks the ladder in even steps; TAG's steps shrink as the card gets
   worse.** TAG's bands run 69 score points wide at the bottom of the ladder and
   27 at the top, so the same increment of damage costs fewer grades the further
   down the card already is. No choice of weights gives PSA that property.

**No number here is PSA's retuned.** PSA's are denominated in ladder steps and
TAG's in score points out of 1000; there is no saturation constant, because a
sub-score is clamped at zero; and there is no `front_centering_share`, because a
scanner does not know which face it is looking at.

## What v0.1.0 claims, and what it deliberately never does

It is a **declared-uncertainty baseline**, on the basis
[ADR 0011](../../../docs/adr/0011-the-v1-grade-predictor-basis.md) fixed. Nothing
is trained and nothing is fitted: `grading_outcomes` holds zero rows, and TAG's
published standard — its band table included — is that company's copyrighted
reference data, which this repository does not reproduce. The one anchor
`companies.py` cites is nowhere near enough to fit seventeen band edges against,
so the curve is a declared prior chosen for its behaviour. **That the bands are
non-uniform is the claim; the particular widths are not.**

**A thin assessment widens the distribution; it never refuses.** There is no
coverage gate and no confidence gate. An assessment with every category refused
is still an assessment and produces a very wide answer; the only refusal in this
step is a refusal on the way in, which the caller propagates without ever
building an assessment (#227). `model_confidence` is bounded above both by the
assessment's own confidence and by a declared per-version ceiling.

**No bucket.** §24's `7_or_lower` stays legal everywhere it is legal today; this
predictor spreads that mass over the tail grades instead.

**No designation.** TAG issues grade 10 under two names — Pristine and Gem Mint —
and `companies.py` is explicit that *"the prediction side does not read them and
must not start"*. A designation is something a slab already carries; §24's output
is a distribution over grades, and this predictor answers `10`.

**ADR 0006's TAG refusal stops at the market boundary.** TAG market data is
`insufficient_information` for all of V1. That is a statement about what a
provider sells, and it has nothing to do with predicting a grade; letting it reach
this package would make TAG unrenderable for a reason spec §24 never asked for.

`manufacturing_defects` and `eye_appeal` are read by nothing here — the first is
derived from surface and edges and would be deducted for twice, the second is
refused by construction.

## Versioning

The V1 implementation is a heuristic versioned by `GRADING_TAG_VERSION` — a code
constant, never a `model_bundles` row, because a row names a trained artifact with
a dataset version and metrics. Changing any threshold bumps it.

```text
grading-tag-heuristic-v0.1.0   this baseline
grading-tag-v0.2.0             spec §59's grammar: the trained bundle that replaces it
```

Never `/latest/`. The first trained model arrives through the same injected
predictor seam and supersedes this constant with a registry version; nothing here
has to be undone for it.
