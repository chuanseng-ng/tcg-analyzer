# `packages/grading-companies`

The `GradingCompanyAdapter` port (spec §22) and the PSA / TAG / BGS grade
scales. It lands in M4 rather than with the grading models in M8 because spec
§35 keys a graded market price by `(grading_company, grade)`, and that key
cannot be written until something says which grades each company issues.

Depends on `tcg-domain` and nothing else. An adapter here is published
reference data, not a client: nothing in this package reaches a network, a
database or a vendor SDK.

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

## What the adapters do not answer yet

- `predict_grade()` raises `GradePredictionUnavailable`. Spec §24's per-company
  models arrive in M8; a fabricated distribution would be exactly the
  confidently-wrong output the product exists to avoid.
- `get_service_options()` is empty. Grading fees are configurable economic
  inputs (spec §45) and belong to M5's economic configuration, not to a table
  here that would go stale and disagree with whatever the engine was told.
- `get_rules()` carries a version, a source and a read date, and an empty rules
  body. The published standards are the companies' copyrighted text; what spec
  §57 records against an analysis is the version identifier.
