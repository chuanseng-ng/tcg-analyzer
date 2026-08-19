# `packages/domain`

The framework-free core: `GradeDistribution`, `Money`, the TCG-agnostic card
reference, the canonical catalog entities and their repository port, the card
database version an analysis records for reproducibility, `Confidence` and the
`insufficient_information` sentinel.

**Zero framework, database or provider dependencies.** `GradeDistribution` must
be impossible to construct in an invalid state — the single most load-bearing
invariant in the codebase.

`game` is a field, not a constant. V1 ships Pokémon only; nothing internal
hard-codes that — `Game` and `Language` name the values V1 ships without
closing the fields to any other.

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
| `Game`, `Language` | `StrEnum`s naming what V1 ships. Members *are* `str`, and every field stays typed `str`, so they are a vocabulary rather than a restriction — a value neither names is still accepted. |
| `POKEMON`, `ENGLISH`, `JAPANESE` | Convenience constants — `"pokemon"`, `"en"`, `"ja"`. Not restrictions: `game` is a validated slug field, so another TCG needs no code change. |
| `Set`, `Card`, `CardExternalId` | Spec §10's canonical catalog, provider-independent. A `Card` holds its `Set` and yields its printed `CardReference`; a `CardExternalId` is where a source such as `tcgdex` or `manual` enters ([ADR 0004](../../docs/adr/0004-the-canonical-card-catalog-source.md)). No catalog images and no row timestamps — see the module docstring for why each is absent. |
| `SetId`, `CardId` | Our identifiers, distinct `NewType`s over `UUID` so one cannot be passed where the other is wanted. A provider's identifier is a `CardExternalId`, never one of these. |
| `CardIdentification` | Spec §20's conclusion: a candidate `Card` and a **required, validated** `Confidence`. A bare `0.82` is rejected — an optional confidence makes "never silently use an uncertain identification" easy to violate. |
| `CardRepository` | The catalog port — `get`, `search`, `external_ids`, all `async`, `Protocol` not ABC. The PostgreSQL adapter lives outside this package. Search is ordered by a total key so paging cannot drop or duplicate a row. |
| `CardDatabaseVersion` | Spec §57's `card_database_version` — one import run, with its source, licence, upstream revision, when the data was made and how much of it there was. The identifier is explicit and ordered (`pokemon-catalog-v0.3.0`); a value naming a moving target is refused outright, because spec §31's rule against `/latest/` is exactly what a reproducibility record cannot survive. Publication order and row bookkeeping stay in the table. |
| `CardDatabaseVersionRepository` | The version port — `current`, `get`, both `async`. Read-only: V1's writers are the seed loader and the import pipeline, each of which already owns the transaction that writes the catalog a version describes. |
| `VERSION_PATTERN` | The identifier grammar, exposed so a writer can check one before building a record. |
| `CardQuery`, `CardPage` | The search's two shapes. Every filter optional and ANDed; an empty filter is rejected rather than treated as a wildcard. `DEFAULT_SEARCH_LIMIT` and `MAX_SEARCH_LIMIT` bound every caller of the port, not merely of the endpoint. |
| `Confidence` | A validated `[0, 1]` value, with `is_below(threshold)` for the places the pipeline must not act on a weak signal. |
| `InsufficientInformation`, `INSUFFICIENT_INFORMATION`, `Uncertain[T]` | "We cannot tell" as a first-class **result** (spec §2.7). Never an exception, never raised, and falsy so `if result:` cannot mistake it for an answer. |
| `DomainError` and subclasses | `InvalidGrade`, `InvalidGradeDistribution`, `InvalidMoney`, `CurrencyMismatch`, `InvalidConfidence`, `InvalidCardReference`, `InvalidCatalogRecord`, `InvalidCardIdentification`, `InvalidCardSearch`. `InvalidCatalogRecord` covers
the version record too — it is built by the same importer as the rows it counts.
Each also derives from `ValueError`. `CatalogUnavailable` is the one that is not about invalid input: it is what a `CardRepository` raises instead of leaking a driver exception, and derives from `ConnectionError`. |

## Tests

`uv run pytest packages/domain` from the repository root.
`tests/test_domain_purity.py` imports the package in a subprocess and fails if
any non-stdlib module appears in `sys.modules` — that is what keeps the
dependency list empty.

Populated in M0 (#16); the card catalog and its port in M1 (#24).
