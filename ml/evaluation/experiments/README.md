# `ml/evaluation/experiments`

Spec §61's experiment log, as a file the repository carries rather than a
platform: one committed JSON per evaluation run, named
`{dataset version}+{evaluation version}.json`. A record is **never
overwritten** — the writer refuses an existing file, because a rerun that
would change the numbers should have bumped a version constant, which changes
the filename.

Two record families, one per harness, because they score different things from
different inputs and move independently:

| Family | Written by | Scores |
| --- | --- | --- |
| `…+condition-evaluation-vX.Y.Z.json` | `uv run tcg-evaluate-condition --version <dataset-version>` | M7's four condition analyzers (#188) |
| `…+grade-evaluation-vX.Y.Z.json` | the grade runner, which lands with the first predictor (#223 to #225) | M8's per-company grade distributions (#222) |

**A grade record's version constant is `GRADE_EVALUATION_VERSION`, not
`EVALUATION_VERSION`.** Bumping one never renames the other's records: a
condition scoring rule changing says nothing about a grade scoring rule, and
tying them would invalidate a committed record whose numbers nothing touched.

§61's eight fields, mapped for a scored heuristic:

| §61 asks for | Here |
| --- | --- |
| dataset | `dataset_version` + `split_seed` |
| model | `condition_version` (the compose version plus all four analyzer versions) and `evaluation_version` (the harness's own) — heuristic versions are code constants, never registry rows |
| hyperparameters | `analyzer_thresholds` (the four default-threshold records, prefix-merged) and `thresholds` (the harness's scoring constants) |
| metrics | `splits` (per split, per axis, per label, counts beside every figure) and `composition` |
| git commit | `git_commit` (`git rev-parse HEAD`, or `--commit` inside a container) |
| hardware, training duration, checkpoint | **Deliberately absent** — nothing is trained and no artifact is produced; they apply from the first learned model (#189's registry owns checkpoints) |

Every refusal in a record is the one-key
`{"insufficient_information": <reason>}` object. A metric over zero samples
is such a refusal, never a number.

The per-axis `refused` maps are keyed on the analyzers' own reason strings,
some of which are prose sentences — so rewording a reason in an analyzer
changes the keys in later records. That is a behavior-adjacent change even
when nothing numeric moved; treat it like any other analyzer edit (it bumps
that axis's version, and the new record lands under the new filename).

A grade record's §61 mapping is the same table with two substitutions: the
model field is `grade_evaluation_version` plus the per-company
`model_versions` each split records, and the hyperparameter field is
`thresholds` — `within_one_target`, `wilson_z` and `calibration_bins`. There is
no `analyzer_thresholds`, because a grade predictor's own constants are
recorded beside its output rather than here.

Committing a run is its own commit, the manifests' convention:
`chore(ml/evaluation): record <dataset version> at <model version>`.
