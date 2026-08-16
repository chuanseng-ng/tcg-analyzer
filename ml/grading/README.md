# `ml/grading/`

Per-company grade predictors. Each company gets its own model consuming the
neutral condition representation.

Every predictor outputs a **probability distribution over grades**, retained in
full, satisfying `0 <= P(g) <= 1` and `Σ P(g) ≈ 1` (spec §63). A single
predicted grade is never the model's output contract.

Grouping directory — not itself a package.
