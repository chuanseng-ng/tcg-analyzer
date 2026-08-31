# `ml/evaluation/experiments`

Spec §61's experiment log, as a file the repository carries rather than a
platform: one committed JSON per evaluation run, written by
`uv run tcg-evaluate-condition --version <dataset-version>` and named
`{dataset version}+{evaluation version}.json`. A record is **never
overwritten** — the command refuses an existing file, because a rerun that
would change the numbers should have bumped a version constant, which changes
the filename.

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

Committing a run is its own commit, the manifests' convention:
`chore(ml/evaluation): record <dataset version> at <condition version>`.
