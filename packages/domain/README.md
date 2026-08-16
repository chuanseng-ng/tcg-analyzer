# `packages/domain`

The framework-free core: `GradeDistribution`, `Money`, the TCG-agnostic card
reference, `Confidence` and the `insufficient_information` sentinel.

**Zero framework, database or provider dependencies.** `GradeDistribution` must
be impossible to construct in an invalid state — the single most load-bearing
invariant in the codebase.

`game` is a field, not a constant. V1 ships Pokémon only; nothing internal
hard-codes that.

Populated in M0 (#16).
