# `database/seeds`

Reference data loaded into a fresh database: the card catalog subset below, the
published grading rules versions, and later grading companies and cost
defaults.

Seeds are data, not schema. They must be idempotent.

## The card catalog

```bash
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
uv run alembic upgrade head
uv run tcg-seed-catalog
```

`catalog/sets.json` and `catalog/cards.json` hold roughly twenty hand-authored
English and Japanese Pokémon cards under the provider key `manual`. The loader
is `services/api/src/tcg_api/catalog/seed.py`.

They are the catalog a developer gets without a network, and ADR 0004 keeps
them as the floor if the TCGdex position ever has to be withdrawn. **They are
not an authoritative card database.** The canonical source is TCGdex, per
`docs/adr/0004-the-canonical-card-catalog-source.md`, and
`uv run tcg-import-catalog` is what reads it; these rows are written by hand and
are wrong in whatever ways hand-written data is wrong.

### These fixtures and an import are alternatives, not layers

A directory holding `sets.json` and `cards.json` is a *snapshot*, and the
importer writes one in this same shape plus a `catalog.json` manifest carrying
its provenance. `services/api/src/tcg_api/catalog/snapshot.py` is the format,
the derived identifiers and the only path that writes a catalog; `seed.py` and
`import_catalog.py` are both callers of it.

Because identifiers are derived from `(game, language, set_code, card_number,
variant)`, the two sources merge wherever they agree — TCGdex's Base Set
Charizard `4/102` `unlimited-holo` lands on the same row as the `manual`
fixture, carrying both providers' identifiers. Where they disagree they do not:
TCGdex names the plain Base Set Pikachu printing `unlimited-normal` where these
fixtures call it `unlimited`, so loading both leaves two rows for one card.
That is cosmetic rather than incorrect, but a database is normally seeded *or*
imported into, not both.

### What the fixtures deliberately contain

| Property | Where |
| --- | --- |
| Both languages V1 ships | English `BS`, `MEW`; Japanese `SV2a`, `SV3` |
| A variant trio on one card | Base Set Charizard `4/102` — 1st edition, shadowless, unlimited |
| A reverse-holo printing | `MEW` Pikachu and Charizard (reverse holo post-dates Base Set) |
| Cards with no variant at all | the Japanese rows — what `UNIQUE NULLS NOT DISTINCT` protects |
| A card number that needs normalising | `025/165`, which a user types as `25` |
| Japanese names | `リザードン`, `ポケモンカード151`, `黒炎の支配者` |
| One card with two providers | Base Set Charizard carries `manual` and `example` |

The `example` provider is fictional and exists only so that spec §10's reason
for a third table — several external databases referencing one canonical card —
is exercised by data that actually ships. It is never a real source.

No fixture carries `image_front` or `image_back`: ADR 0004 keeps both `NULL` in
V1, because the only card images this product shows are the user's own uploads.

### Identifiers are derived, never authored

A set's id is `uuid5` over `game/language/set_code`; a card's over
`game/language/set_code/card_number/variant`. Two consequences:

- **The loader is idempotent** without looking anything up. A second run upserts
  onto the first rather than writing a second catalog, and correcting a name in
  a fixture and re-running converges on the fixture.
- **Other code can predict an id.** `seed_card_id("pokemon", "en", "BS",
  "4/102", "1st-edition-holo")` is a fact about these files, computable without
  a database.

Changing the namespace UUID in `snapshot.py` re-keys everything and orphans
every row a previous run wrote. Don't.

### The catalog version

Loading the fixtures also publishes `pokemon-catalog-seed-v0.0.0` into
`card_database_versions` — spec §57's `card_database_version`, so a deployment
running on these fixtures can say so rather than being mistaken for one running
on the real catalog. Its counts come from the fixtures themselves rather than
from `count(*)`, because they describe what *this* run wrote.

It is written with `ON CONFLICT DO NOTHING`, which reverses the policy the three
statements above use. That is deliberate. Correcting a card's name and re-running
should converge on the fixture; rewriting a published version would falsify every
analysis that recorded it, and the table's trigger refuses the `UPDATE` regardless.

**So bump `SEED_CATALOG_VERSION` when the fixtures change materially.** Adding or
removing one without bumping it leaves a published version describing content it
no longer matches, and the loader warns rather than guessing which of the two you
meant. Move `SEED_CATALOG_GENERATED_AT` with it, never on its own.

### What the loader will not do

It never creates or drops anything, and it will not delete a row that has left
the fixtures. Removing seed data is a migration's job.

## The published grading rules

```bash
export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
uv run alembic upgrade head
uv run tcg-seed-grading-rules
```

Spec §23's `grading_rules` — one published grading standard, of one company, at
one version. The loader is
`services/api/src/tcg_api/grading/seed.py`.

**There is no JSON file here, deliberately.** The catalog fixtures are JSON
because a hand-authored card list has no Python home; these records do. They are
`PSA_RULES`, `TAG_RULES` and `BGS_RULES` in `packages/grading-companies`, each a
validated `GradingRules` already carrying its version, its source URL and the
date the source was read. A file beside them would be a second source of truth
for the same three records, and a lossy one — it would have to re-encode
`effective_from: null` for TAG and BGS, and need a parser and a schema whose
whole job is to rebuild objects the process already holds.

The loader reads them through `ADAPTERS` rather than by name, so a fourth
grading company costs one new adapter and no edit here.

**The rules body is `{}`, and that is a decision rather than a gap.** Each
company's grading standard is that company's copyrighted text and this
repository does not reproduce it. What spec §57 needs is the *identifier* — so a
run made today can be told apart from one made after a company revises its
standard — plus a source a human can open. Both are columns.

Written with `ON CONFLICT DO NOTHING`, for the reason the catalog version above
gives: rewriting a published version would falsify every analysis that recorded
it. Here the table's trigger refuses an `UPDATE` outright, so the policy is the
database's rather than the loader's — a published version is corrected by
publishing a successor with a new `effective_from`, never by editing.

**`verified_on` carries ADR 0006's ninety-day rule into reference data.** A
record older than ninety days is re-read before anything relies on it. One
caveat travels with the BGS row: beckett.com refused both an automated fetch and
a real browser on 2026-08-24, so that scale rests on a search index of Beckett's
own page rather than on the page itself. Confirm it by hand before anything
treats a BGS price as authoritative.
