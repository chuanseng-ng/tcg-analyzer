# ADR 0004 — The canonical card catalog source

- **Status:** accepted
- **Date:** 2026-08-18
- **Refs:** M1, #25, spec §2.4, §2.5, §10, §77

## Context

M1 builds the canonical card catalog — the records a user searches in order to
say "this is the card I am holding". Nothing else in the milestone can be
populated until it is settled where those records come from, and the answer is
constrained from three directions at once.

**The catalog must be provider-independent.** §2.4 and §77 forbid any card
database becoming a hard dependency of the core domain. Whatever is chosen
enters through `card_external_ids` and must be replaceable without touching
`packages/domain`.

**Provenance is mandatory.** §2.5 requires documented usage rights for external
data used in commercial-capable development, and states that data must not be
incorporated merely because it is publicly accessible. A reachable API is not a
licensed one.

**V1 ships English *and* Japanese.** This is the criterion that removed most of
the field. Japanese sets are not translations of English sets — they are
different sets, with their own numbering — so a source that covers one language
covers roughly half the product.

A limit applies to every candidate equally, and naming it first keeps the rest
of this record honest: **none of them can license the underlying intellectual
property.** Pokémon card names, artwork, set names and card layouts belong to
The Pokémon Company, Nintendo, Creatures Inc. and GAME FREAK, and the projects
below say so themselves. The question is therefore not "who owns Pokémon card
data" — nobody in this list does — but "whose compilation may we copy, and what
exactly are we copying from it".

What we copy is facts: game, language, set, card number, name, variant, rarity,
illustrator. Individual facts are not copyrightable. Singapore's Copyright Act
2021 protects a compilation only through the originality of the *selection or
arrangement* of its contents, and Singapore has no sui generis database right of
the kind EU law grants. We take the facts and impose our own schema and
arrangement on them. This ADR is an engineering record, not legal advice; if the
product moves toward monetisation the position should be reviewed by someone
qualified to give it.

### The options that were on the table

**pokemontcg.io (Pokémon TCG API v2).** The obvious default and the most widely
used. Two disqualifications. Its data repository, `PokemonTCG/pokemon-tcg-data`,
carries **no licence file at all** — the absence of a licence is all rights
reserved, not permission — and its `cards/` directory contains `en` only, so it
cannot serve the Japanese half of V1. As of 2026-08-18 the service is
additionally "now part of Scrydex", with an end-of-life for the old platform
anticipated but not yet dated.

**Scrydex**, the official paid successor to pokemontcg.io. It buys a vendor
relationship and explicit terms, which is genuinely worth something. But its
published multi-language roadmap names Chinese, Korean, Portuguese, French,
German and Italian — **Japanese is absent** — and its terms of service could not
be read without an account (`dev.pokemontcg.io/terms` refuses unauthenticated
requests). Terms we cannot read cannot be assessed against §2.5, and pricing
starts at USD 29/month, which would make the catalog a paid hard dependency
before the product has a user.

**TCGplayer-derived mirrors** such as TCGCSV. Ruled out upstream: TCGplayer
access is not granted to this project and its terms restrict competing
commercial products.

**Price-first vendors** — JustTCG, PokemonPriceTracker, PokéWallet, apitcg.
These answer M3's question, not this one. Taking a catalog from a price vendor
would tie the card database to a pricing contract, which is exactly the coupling
§2.4 exists to prevent. A catalog source and a price source are separate
decisions and may well be different vendors.

**Bulbapedia.** Excellent coverage, wrong licence: CC BY-NC-SA 2.5. The
non-commercial clause is disqualifying for a commercial-capable product, and no
amount of attribution cures it.

**The official sites** — `pokemon-card.com` for Japanese, `pokemon.com` for
English. Authoritative and unlicensed. Scraping them would be the clearest
possible violation of "public accessibility is not permission".

**The hand-authored fixtures from #23** — roughly twenty English and Japanese
cards under a `manual` provider, written for this repository. Zero licence risk
and entirely ours, but twenty cards is a test harness, not a catalog.

**TCGdex.** A community-maintained multi-language Pokémon TCG database,
MIT-licensed, covering English in `data/` and the Japanese and other Asian
printings in `data-asia/` as distinct sets. Free, no API key, and — because the
repository and its server are both open — runnable locally if the hosted API
ever stops.

## Decision

**TCGdex is the canonical card catalog source for V1.** Data comes from
`github.com/tcgdex/cards-database`, read through `api.tcgdex.net` or a
self-hosted instance of the same server.

It enters the system as a replaceable provider and nothing more. Imported
records are keyed into `card_external_ids` under the provider key `tcgdex`.
There is no TCGdex-shaped column on `cards`, no TCGdex type in
`packages/domain`, and no import-time dependency anywhere the request path can
reach.

**Rights relied upon.** The MIT licence, `Copyright (c) 2021 TCGdex`, covers the
compilation and code. Against the rights this decision had to confirm:

| Right | Position |
| --- | --- |
| Commercial use | Permitted by MIT |
| Storage in our own database | Permitted by MIT |
| Caching | Permitted, and the project's own documentation asks bulk consumers to cache locally rather than re-fetch |
| Display of imported text | Permitted by MIT for the compilation; the underlying names remain TPC trademarks |
| Derived data | Permitted by MIT |
| Redistribution | Permitted by MIT |
| Attribution | **Required** — the copyright notice and licence text must travel with any substantial portion, and are recorded here and in each import's provenance record |

**No catalog card images in V1.** Reference images are neither hotlinked from
`assets.tcgdex.net` nor mirrored into our own object storage. MIT covers
TCGdex's compilation; it cannot and does not grant rights over The Pokémon
Company's artwork, and a research ADR is not the place to assume them. The
`image_front` and `image_back` columns of the §10 schema stay in place and stay
`NULL`. The only card images V1 displays are the ones the user uploaded.

**Every import run records its provenance.** Source, licence reference (`MIT`),
retrieved-at, the upstream repository commit imported, and record counts are
written into the immutable `card_database_version` record defined by #27. No
competing provenance table.

**Continuity plan: self-host.** Because the repository is MIT and the API server
ships a `Dockerfile` and `docker-compose.yml`, the hosted API disappearing is a
degradation of convenience, not a loss of data. The imported catalog already
lives in our database and the source of truth can be rebuilt from a git clone.

**Fallback, and a disclosed input.** The hand-authored seed fixtures from #23
remain under the `manual` provider. They are what M1's API and UI work runs
against while the import pipeline is built, and they remain the floor if the
TCGdex position ever has to be withdrawn.

## Consequences

**What this makes easy.** English and Japanese arrive from one source in one
shape, with Japanese sets modelled as distinct sets — which is how our schema
already wants them. There is no key to provision, no account, no bill, and no
vendor approval step between deciding to import and importing. The licence is
one of the most permissive available and is unambiguous about the four rights
that mattered: commercial use, storage, derived data, redistribution. If the
service degrades we can run it ourselves.

**What this makes expensive.** The data is community-maintained. There is no
SLA, no warranty — MIT disclaims one explicitly — no published rate limit to
plan capacity against, and no contractual recourse when a set lands late or a
field is wrong. Per-card completeness varies, older sets carrying fewer
localised names than recent ones, so the import pipeline must tolerate missing
optional fields rather than assume a uniform record. Japanese and English cards
being separate sets means a cross-language link between "the same card in two
languages" is not supplied and would have to be derived if it is ever needed.

**What this forecloses.** No reference image on the search results, card detail
or identification-confirmation screens until the image-rights question is
answered on its own terms. That makes the M1 confirmation screen text-forward —
set, number, variant, rarity — where a picture would have been more immediate.
Issues #29, #30 and #91 are adjusted accordingly.

The decision is scoped to the catalog. It says nothing about market prices
(M3, #4) and nothing about training data, where the §29 provenance gate applies
a stricter test than this one: catalog images used for anything beyond display
must be evaluated there, separately, and this ADR must not be cited as having
settled it.

## Evidence

Captured 2026-08-18. Licence and coverage facts for the two GitHub-hosted
candidates were read from the GitHub API rather than from project marketing.

| Candidate | English | Japanese | Licence | Verdict |
| --- | --- | --- | --- | --- |
| [TCGdex](https://github.com/tcgdex/cards-database) | yes, `data/` | yes, `data-asia/` — 36 series including `SV1S`, `SV1V`, `SV1a`, `SV10`, `SV11B`, `SV11W` | MIT, `Copyright (c) 2021 TCGdex` | **Selected** |
| [pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) | yes | no — `cards/en` only | **none** (all rights reserved) | Rejected |
| [Scrydex](https://scrydex.com/) | yes | not offered; absent from the stated roadmap | proprietary, paid, terms not publicly readable | Rejected |
| TCGCSV and other TCGplayer-derived mirrors | yes | partial | TCGplayer terms restrict competing commercial products | Rejected |
| JustTCG, PokemonPriceTracker, PokéWallet, apitcg | yes | some | vendor terms; catalog rights unstated | Out of scope — M3 |
| [Bulbapedia](https://bulbapedia.bulbagarden.net/wiki/Bulbapedia:Copyrights) | yes | partial | CC BY-NC-SA 2.5 | Rejected — non-commercial |
| `pokemon-card.com`, `pokemon.com` | yes | yes | none; scraping | Rejected |
| #23 seed fixtures | ~20 cards | ~20 cards | ours | Retained as fallback |

Supporting observations:

- `PokemonTCG/pokemon-tcg-data` returns `license: null` from the GitHub API and
  its `cards/` directory contains a single entry, `en`.
- `tcgdex/cards-database` returns `license: MIT`; its `LICENSE` file is the
  standard MIT text with `Copyright (c) 2021 TCGdex`.
- The TCGdex API is documented as free and keyless, with no published hard rate
  limit and an explicit request that bulk consumers cache locally.
- TCGdex states that it is not produced, endorsed, supported or affiliated with
  Nintendo or The Pokémon Company, and that the card content remains their
  property. This ADR relies on that statement rather than contradicting it.
- `pokemontcg.io` presents itself as "now part of Scrydex"; the Scrydex FAQ
  calls itself the official successor and anticipates announcing an end-of-life
  date for the older platform.
