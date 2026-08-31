# ml/evaluation

The benchmark and calibration harness — spec §26's defect-detection metrics,
§21-adjacent centering agreement, and §25's probability-quality tools (Brier
score, log loss, expected calibration error, reliability bins), applied to
what the M7 condition analyzers emit. "Model selection must be
benchmark-driven rather than predetermined" (§8) is this project's rule, and
this package is the benchmark: **a learned replacement for any axis analyzer
enters only by beating the classical baseline here, behind the same
signature.**

A dataset version's manifest in, per-split metrics out. `load_manifest`
parses a committed `datasets/manifests/*.json` (the one seam ADR 0009 allows
`ml/*` — never the database, never object storage, and a purity test holds
this package to stdlib + `tcg-domain`). `evaluate` scores the analyzers'
outputs, which the caller produces: `tcg-evaluate-condition` in
`services/api` is the worker-image command that resolves bytes and card
frames, runs the four analyzers and the public `compose`, and writes one
report per run into `experiments/`.

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
- **Versioned constants**: `IOU_THRESHOLD = 0.5` (boxless truth matches by
  label), `CENTERING_AGREEMENT_TOLERANCE = 0.05` ratio points,
  `CALIBRATION_BINS = 10`. Changing any of them, or any scoring rule, bumps
  `EVALUATION_VERSION`.
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
experiment log as a file the repository carries, not a platform. See
`experiments/README.md` for the field contract.
