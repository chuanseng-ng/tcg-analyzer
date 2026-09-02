# `ml/grading/psa`

Predicts the grade PSA would issue for one assessed card, as a probability
distribution — spec §24, issue #223.

`predict(assessment)` takes M7's neutral `ConditionAssessment` (#180, #186) and
answers a `GradePrediction`: a `GradeDistribution` over **every one of PSA's
eighteen grades**, a `model_confidence`, and a `model_version`. Never a single
expected grade — the distribution is the value, and `most_likely_grade` is a
view of it.

## What v0.1.0 claims, and what it deliberately never does

It is a **declared-uncertainty baseline**, on the basis
[ADR 0011](../../../docs/adr/0011-the-v1-grade-predictor-basis.md) fixed. Each
axis of the assessment yields a damage fraction; each declared weight says how
many *ladder steps* a fully damaged axis costs at PSA; the weighted sum walks a
centre down from the top of `PSA_SCALE.ordered`, and a Gaussian in ladder steps
spreads the mass around it. Nothing is trained and nothing is fitted:
`grading_outcomes` holds zero rows, and PSA's published standard is that
company's copyrighted text, which this repository does not reproduce.

**A thin assessment widens the distribution; it never refuses.** There is no
coverage gate and no confidence gate. An assessment with every axis refused is
still an assessment and produces a very wide answer; the only refusal in this
step is a refusal on the way in, which the caller propagates without ever
building an assessment (#227). `model_confidence` is bounded above both by the
assessment's own confidence and by a declared per-version ceiling — an
uncalibrated mapping is not entitled to more, however clean the card.

**No bucket.** §24's `7_or_lower` stays legal everywhere it is legal today; this
predictor spreads that mass over the tail grades instead, because that says
exactly the same thing in the vocabulary `grading_outcomes`, the market ladder
and #222's benchmark already speak.

**No PSA subgrades, no designation.** "Authentic" is issued *in place of* a
number and V1 does not authenticate cards; the predictor emits grades only.

`manufacturing_defects` and `eye_appeal` are read by nothing here — the first is
derived from surface and edges and would be counted twice, the second is refused
by construction.

## Versioning

The V1 implementation is a heuristic versioned by `GRADING_PSA_VERSION` — a code
constant, never a `model_bundles` row, because a row names a trained artifact
with a dataset version and metrics. Changing any threshold bumps it.

```text
grading-psa-heuristic-v0.1.0   this baseline
grading-psa-v0.2.0             spec §59's example: the trained bundle that replaces it
```

Never `/latest/`. The first trained model arrives through the same injected
predictor seam and supersedes this constant with a registry version; nothing
here has to be undone for it.
