# `packages/domain`

The framework-free core: `GradeDistribution`, `Money`, the TCG-agnostic card
reference, `Confidence` and the `insufficient_information` sentinel.

**Zero framework, database or provider dependencies.** `GradeDistribution` must
be impossible to construct in an invalid state — the single most load-bearing
invariant in the codebase.

`game` is a field, not a constant. V1 ships Pokémon only; nothing internal
hard-codes that.

## Public surface

Everything below is re-exported from `tcg_domain`; nothing else is public.

| Name | What it is |
| --- | --- |
| `Grade`, `GradeBound` | One term of a distribution — `"10"`, `"9.5"`, `"7_or_lower"` (spec §24). Sortable, so a UI orders a distribution without reimplementing the rule. `Grade.parse(str)` and `str(grade)` are inverses. |
| `MIN_GRADE`, `MAX_GRADE` | The bounds of the scale, `0` and `10`. |
| `GradeDistribution` | A probability distribution over grades, validated **in the constructor** against spec §63: non-empty, every `P(g)` in `[0, 1]` and finite, no grade twice, and `Σ P(g)` within `SUM_TOLERANCE` of 1. Retained in full; `most_likely_grade` is a view of it, never a replacement (spec §2.1). Crosses the API boundary via `from_mapping` / `as_mapping`. |
| `SUM_TOLERANCE` | `1e-6` — how far `Σ P(g)` may drift from 1. |
| `Money`, `Currency` | Exact `Decimal` amounts quantised to the cent with `ROUND_HALF_UP`. A `float` is rejected outright, in the constructor and in `*`. Mixed-currency arithmetic raises `CurrencyMismatch`. SGD-only in V1; the field exists so another currency is configuration, not a rewrite. |
| `CardReference` | `game`, `language`, `set_code`, `card_number`, `variant`. Set codes and card numbers are recorded verbatim. |
| `POKEMON`, `ENGLISH`, `JAPANESE` | Convenience constants — `"pokemon"`, `"en"`, `"ja"`. Not restrictions: `game` is a validated slug field, so another TCG needs no code change. |
| `Confidence` | A validated `[0, 1]` value, with `is_below(threshold)` for the places the pipeline must not act on a weak signal. |
| `InsufficientInformation`, `INSUFFICIENT_INFORMATION`, `Uncertain[T]` | "We cannot tell" as a first-class **result** (spec §2.7). Never an exception, never raised, and falsy so `if result:` cannot mistake it for an answer. |
| `DomainError` and subclasses | `InvalidGrade`, `InvalidGradeDistribution`, `InvalidMoney`, `CurrencyMismatch`, `InvalidConfidence`, `InvalidCardReference`. Each also derives from `ValueError`. |

## Tests

`uv run pytest packages/domain` from the repository root.
`tests/test_domain_purity.py` imports the package in a subprocess and fails if
any non-stdlib module appears in `sys.modules` — that is what keeps the
dependency list empty.

Populated in M0 (#16).
