# `packages/grading-companies`

The `GradingCompanyAdapter` port (spec §22) and the PSA / TAG / BGS grade
scales. It lands in M4 rather than with the grading models in M8 because spec
§35 keys a graded market price by `(grading_company, grade)`, and that key
cannot be written until something says which grades each company issues.

Depends on `tcg-domain` and nothing else. An adapter here is published
reference data plus whatever grading model it was handed, not a client: nothing
in this package reaches a network, a database or a vendor SDK, and nothing in
it imports a model.

## The grade scales

| Company | Grades | 9.5? | Count |
| --- | --- | --- | --- |
| PSA | 1, 1.5, 2, 2.5 … 8, 8.5, 9, 10 | no | 18 |
| TAG | 1, 1.5, 2, 2.5 … 8, 8.5, 9, 10 | no | 18 |
| BGS | 1, 1.5, 2, 2.5 … 9, 9.5, 10 | **yes** | 19 |

All three issue half grades. The grade they disagree about is **9.5** — which
is the opposite of the usual summary, "BGS has half grades and PSA and TAG do
not", and code written to that is wrong for sixteen of PSA's eighteen grades.

Three designations are deliberately not modelled: PSA "Authentic", BGS Black
Label, and TAG's split of grade 10 into Pristine and Gem Mint. None is a point
on a scale, and each would have to widen `tcg_domain.Grade` beyond a `Decimal`
multiple of 0.5 — the property that makes a grade usable as a distribution key.
`companies.py` records the sources and the date each was read — including
that beckett.com refused both an automated fetch and a real browser on
2026-08-24, so the BGS scale is the one entry not read from the company's
own page.

## `predict_grade()` — the model is injected, never imported

An adapter is built with an optional `GradePredictor`, a plain callable
`(ConditionAssessment) -> Uncertain[GradePrediction]`, and answers spec §22's
fifth responsibility by consulting it: the answer is validated against that
adapter's own scale, a model's refusal (`INSUFFICIENT_INFORMATION`) is returned
as a result, and a model's own exception is translated into
`GradePredictionFailed` so no caller's error handling depends on which model is
behind the port. `ml/grading/{psa,tag,bgs}` each export the callable as
`predict` and depend on this package for it — never the reverse (ADR 0011
decision 5), which is what keeps `dependencies = ["tcg-domain"]` true.

The module-level `ADAPTERS` are built **without** one and raise
`GradePredictionUnavailable`. That registry is what the API image imports for
`GET /grading-companies`, and the API image carries no model; the worker builds
the predicting adapters in its own wiring module (#227). A fabricated
distribution would be exactly the confidently-wrong output the product exists
to avoid.

## What the adapters do not answer

- `get_service_options()` is empty. Grading fees are configurable economic
  inputs (spec §45) and belong to M5's economic configuration, not to a table
  here that would go stale and disagree with whatever the engine was told.
- `get_rules()` carries a version, a source and a read date, and an empty rules
  body. The published standards are the companies' copyrighted text; what spec
  §57 records against an analysis is the version identifier.
