# ml/evaluation

The benchmark and calibration harness — spec §26's defect-detection and
grade-prediction metrics, §21-adjacent centering agreement, and §25's
probability-quality tools (Brier score, log loss, expected calibration error,
reliability bins). "Model selection must be benchmark-driven rather than
predetermined" (§8) is this project's rule, and this package is the benchmark:
**a learned replacement for any axis analyzer or grade predictor enters only by
beating the classical baseline here, behind the same signature.**

There are **two harnesses, separately versioned**, because they score different
things from different inputs:

| Harness | Scores | Version constant |
| --- | --- | --- |
| `evaluate` (#188) | M7's four condition analyzers against the manifest's annotation rows | `EVALUATION_VERSION` |
| `evaluate_grades` (#222) | M8's per-company grade distributions against the grades companies issued | `GRADE_EVALUATION_VERSION` |

`evaluate_grades` lands **before** the predictors it judges (#223 to #225) —
#188's rule, so that a predictor PR quotes a number instead of promising one.
`grading_outcomes` holds zero rows today, so every figure that needs an issued
grade refuses, per figure, and the record still renders.

A dataset version's manifest in, per-split metrics out. `load_manifest`
parses a committed `datasets/manifests/*.json` (the one seam ADR 0009 allows
`ml/*` — never the database, never object storage, and a purity test holds
this package to stdlib + `tcg-domain`). `evaluate` scores the analyzers'
outputs, which the caller produces: `tcg-evaluate-condition` in
`services/api` is the worker-image command that resolves bytes and card
frames, runs the four analyzers and the public `compose`, and writes one
report per run into `experiments/`. `evaluate_grades` scores the three
predictors' distributions the same way: `tcg-evaluate-grading` runs the same
pass, predicts per physical copy, fills each subject's issued grades from its
manifest member, and writes the grade family's record (#242).

## Scoring rules

- **Splits are scored separately and never pooled.** §27 isolates the test
  set; threshold calibration reads train/validation only. Counts ride beside
  every metric, and a class with no examples answers
  `{"insufficient_information": ...}` rather than a number.
- **The truth protocol** (#181, `datasets/schemas/annotation-schema.md`): the
  newest row per `(kind, region)` is the current view for corners and edges;
  surface rows are never collapsed; absence is clean **only on worked-on
  images**; readers filter by `representation` and never project between
  frames (#175) — fine-class truth exists only in rows declaring `original`,
  which are counted and set aside, not scored against artifact-frame
  predictions.
- **Abstentions are counted, not scored.** An `unknown` prediction, an
  `unknown` truth row, and a surface class refused class-level
  (`not_assessed` — the busy-face gate the M7 notes expect to fire on most
  real faces) each land in a count. Those counts are the price of the
  refusals, which is precisely what #188 exists to measure.
- **±1 is a step on the company's own ladder, never ±1.0 on the value.** PSA
  and TAG issue no 9.5 and BGS does, so a BGS 9.5 is one step from 10 and a BGS
  9 is two, while a PSA 9 is one. Reading it arithmetically hands BGS an easier
  target for free (ADR 0011 decision 2).
- **A grade prediction is read two ways, on purpose.** Accuracy reads the
  distribution's own terms through `most_likely_grade`; probability quality
  reads the **ladder projection**, in which a bucket's mass is spread uniformly
  over the scale points it collapses. The projection keeps the class set
  identical for every predictor of one company, which is what makes two of them
  comparable, and it prices coarseness. A bucket is therefore a within-±1 hit
  when it covers the issued grade and never an exact one.
- **§27's target is claimed against an interval, not a rate.** Every within-±1
  figure carries the 95% Wilson score lower bound beside it; `meets_target` is
  that bound against `WITHIN_ONE_TARGET = 0.80`. A flawless 4/4 reports 0.5101,
  and no perfect record clears 0.80 below n = 16. Both constants are ADR 0011's
  — moving either is a new ADR, not an edit.
- **Calibration is reported, never fitted.** §25 requires the figures; nothing
  here fits a calibrator, and calibrating against the test split is forbidden
  (§27). Temperature scaling and an isotonic layer re-enter only behind a
  bumped version constant, on train and validation only.
- **Versioned constants**: `IOU_THRESHOLD = 0.5` (boxless truth matches by
  label), `CENTERING_AGREEMENT_TOLERANCE = 0.05` ratio points,
  `CALIBRATION_BINS = 10`. Changing any of them, or any condition scoring rule,
  bumps `EVALUATION_VERSION`; changing a grade scoring rule, `WITHIN_ONE_TARGET`
  or `WILSON_Z_95` bumps `GRADE_EVALUATION_VERSION`. The two never move
  together.
- **mAP is deliberately absent.** §26 says "where appropriate": a
  confidence-ranked sweep over the current corpus's handful of surface
  markers would be fabricated certainty. It re-enters when the corpus can
  support one, behind a bumped `EVALUATION_VERSION`.

## What this benchmark exists to price

The ceilings the axis issues deferred here, verbatim from the M7 notes:
centering's wall ambiguity, nominal-vs-traced outer-edge bias and near-gate
junk quads; corners' and edges' absolute HSV floors vs exposure,
band-flooding `unknown` flips, foil glare and banded area's shape-blindness;
surface's context gate and busy-face refusals; and the composition's
min-confidence rule, where a single flat-0.5 `unknown` drags a whole
assessment (`dragged_to_flat_unknown_floor` in the report's composition
ledger).

## Experiments

`experiments/` holds one committed JSON per evaluation run — §61's
experiment log as a file the repository carries, not a platform. One record
family per harness, each named for its own version constant. See
`experiments/README.md` for the field contract.
