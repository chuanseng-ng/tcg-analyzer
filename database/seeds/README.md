# `database/seeds`

Reference data loaded into a fresh database: the card catalog subset below, and
later grading rules versions, grading companies and cost defaults.

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

They exist so the rest of M1 — the repository adapter, the search endpoint, the
identification UI — has something real to run against while the import pipeline
is still ahead of us. **They are not an authoritative card database.** The
canonical source is TCGdex, per
`docs/adr/0004-the-canonical-card-catalog-source.md`; these rows are written by
hand and are wrong in whatever ways hand-written data is wrong.

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

Changing the namespace UUID in `seed.py` re-keys everything and orphans every
row a previous run wrote. Don't.

### What the loader will not do

It never creates or drops anything, and it will not delete a row that has left
the fixtures. Removing seed data is a migration's job.
