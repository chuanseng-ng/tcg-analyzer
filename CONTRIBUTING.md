# Contributing

Start with the root [`README.md`](README.md) to get the stack running, and
[`docs/architecture.md`](docs/architecture.md) to learn which constraints are not
negotiable. This file covers how work is proposed, reviewed and landed.

## One primary capability per change

A pull request does one thing, and so does each commit inside it.

```text
feat: add card boundary detection      ← yes
Implement ML system                    ← no
```

Do not bundle a schema migration with a UI change. If a change needs a
preparatory refactor, that refactor is its own commit, and often its own PR.
Small, independently testable changes are the norm here — not because the
project is cautious, but because a reviewer can only meaningfully approve what
they can hold in their head at once.

## Pull requests

Every PR description uses these seven headings, in this order.
[`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) fills
them in for you.

```text
## Purpose
## Scope
## Non-goals
## Dependencies
## Implementation
## Tests
## Acceptance Criteria
```

**Non-goals** is the one people skip and the one reviewers rely on most. It is
where you say what you deliberately did *not* do, so that a reviewer stops
looking for it and a future reader does not mistake the omission for an
oversight.

Before opening a PR, run the same checks CI runs. They are listed under
[Checks](README.md#checks) in the README, and nothing in the pipeline is a step
you cannot reproduce locally.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/).

```text
<type>(<scope>): <subject>

<body>

<footer>
```

**Types.** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`.

**Scopes** come from the repository layout — `web`, `annotation`, `api`,
`analysis`, `market-data`, `ingestion`, `domain`, `economic-engine`,
`grading-companies`, `db`, `datasets`, `infra`, or an ML module such as
`ml/centering` or `ml/grading-psa`. Omit the scope only when the change really is
repository-wide.

**Subject.** Imperative mood — "add", not "added" or "adds" — lowercase, no
trailing period, 72 characters or fewer. It must complete the sentence *"If
applied, this commit will …"*.

**Body.** Wrapped at 72 columns. Explain *why*, not *what*: the diff already
shows what changed, and the reason it changed is the part that is expensive to
recover later. Required whenever the subject alone does not carry it.

**Footer.** `BREAKING CHANGE: <description>` for anything that alters a domain
contract, an API shape or a persisted schema. `Refs: M<n>, spec §<n>` where it
helps a reader find the origin of the work.

```text
feat(ml/centering): add template-aware centering for full-art cards

Borderless and full-art layouts have no symmetric border to measure,
so the previous ratio calculation returned meaningless values. Detect
the frame type first and fall back to insufficient_information when
the template is unrecognised.

Refs: M7, spec §21
```

```text
fix(economic-engine): separate incremental grading decision from investment return

The two were sharing a denominator, which overstated ROI whenever an
acquisition cost was supplied.

BREAKING CHANGE: EconomicResult.roi is replaced by
incremental_roi and investment_roi.
Refs: M5, spec §41-42
```

### Rules specific to this repository

- **Version bumps are their own commits.** Model bundles, grading-rule versions
  and dataset versions are immutable artifacts, so record them explicitly:
  `chore(ml/grading-psa): pin model bundle grading-psa-v0.2.0`.
- **Never commit** model weights, training images, API keys or provider
  credentials. The project may be open-sourced later, so these must stay out of
  the history rather than merely out of the working tree.
  `tests/test_repository_structure.py` rejects weight and image file types, and
  CI scans the history for secrets — treat both as a backstop, not a licence to
  be careless.
- **Do not skip hooks** (`--no-verify`) and do not bypass signing. A failing hook
  is information; fix the cause.
- **Prefer a new commit over amending** once the work has been pushed.
- **No `Co-Authored-By` trailer.** Authorship is already recorded by the
  committer field.

## Definition of Done

A feature is not complete merely because the code works locally.

- [ ] implementation complete
- [ ] tests added
- [ ] documentation updated
- [ ] error handling implemented
- [ ] logging appropriate
- [ ] API contracts documented
- [ ] no secrets committed
- [ ] reproducible locally
- [ ] acceptance criteria verified

Machine-learning changes add five more, because a model that cannot be rebuilt
is a model nobody can fix:

- [ ] evaluation dataset identified
- [ ] metrics recorded
- [ ] model version recorded
- [ ] inference schema stable
- [ ] model artifact reproducible

Every experiment logs its dataset, hyperparameters, hardware, metrics,
checkpoint and git commit.

## Tests

Write the test with the change, in the same PR.

- Economic formulas are unit-tested against **manually calculated** fixtures. A
  fixture generated by the implementation proves only that the code agrees with
  itself.
- The default suite needs no Docker. Tests that need PostgreSQL are marked
  `integration` and skip unless `TCG_API_DATABASE_URL` is set; tests that need
  MinIO are marked `object_storage` and skip unless
  `TCG_API_STORAGE_ENDPOINT_URL` is set.

```bash
uv run pytest                     # everything runnable without Docker
uv run pytest -m integration      # requires PostgreSQL
uv run pytest -m object_storage   # requires MinIO
uv run pytest -m "integration and object_storage"   # requires both: the anonymous journey
pnpm --filter @tcg/web test
pnpm --filter @tcg/web e2e        # requires the Compose stack, seeded: the browser journey
```

## Architectural decisions

If your change settles a question the architecture left open, record it as an
ADR in [`docs/adr/`](docs/adr) using
[`docs/adr/template.md`](docs/adr/template.md), and reference it from the PR. An
architectural decision that lives only in a PR thread is a decision the next
person will re-litigate.
