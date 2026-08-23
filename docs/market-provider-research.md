# Market provider research

Spec §34 forbids hard-coding TCGplayer access and requires that a dedicated
research milestone select a commercially usable market-data provider **before**
production market ingestion is implemented. This document is that research.

It is written in three passes, and it is the only place the evidence lives:

| Pass | Issue | What it adds |
| --- | --- | --- |
| Rubric | [#42](https://github.com/chuanseng-ng/tcg-analyzer/issues/42) | The criteria, the hard requirements, and the evidence standard — **before** any candidate is looked at |
| Survey | [#43](https://github.com/chuanseng-ng/tcg-analyzer/issues/43) | Every candidate scored, and a shortlist of two or three |
| Licensing | [#44](https://github.com/chuanseng-ng/tcg-analyzer/issues/44) | What the shortlisted candidates' terms actually permit |

The decision itself is not here. It is an ADR, written by
[#45](https://github.com/chuanseng-ng/tcg-analyzer/issues/45), which cites this
document rather than restating it.

**The rubric is fixed before the survey begins, and that ordering is the point.**
Deciding the criteria after seeing the options is how a convenient API gets
rationalised into a compliant one. If a criterion turns out to be wrong, change
it in a commit of its own that says why — do not quietly reweight it around a
candidate.

## The evidence standard

A score is worth nothing without a citation, and the citation has to be the
thing itself.

- **Licensing claims are evidenced by the terms text**, quoted, with a URL, the
  terms version or effective date, and the date it was read. A marketing page,
  a FAQ, a blog post, a support-chat reply and a summary written by somebody
  else are **not** acceptable evidence for a licensing claim. They may be cited
  as context, labelled as such.
- **Coverage and rate-limit claims** are evidenced by the provider's own
  documentation, or by an observed response. An observed response records the
  request made and the date.
- **Cost** is evidenced by a published price list. "Contact us" is recorded as
  *unpublished*, which is a finding, not a blank.
- **Every finding carries the date it was verified.** Provider terms change,
  and a stale reading is worse than no reading. A finding older than the ADR by
  more than ninety days is re-verified before the ADR relies on it.
- **Ambiguity is recorded as ambiguity.** Where terms do not clearly permit
  something, the finding is "unclear" and it becomes a risk. It is never
  resolved in the project's favour, and never resolved by inference from a
  provider's observed tolerance of somebody else doing it.

## Hard requirements

These are disqualifying. A candidate that fails any one of them is eliminated
regardless of how it scores elsewhere — **no amount of coverage compensates for
terms that forbid the use.** Three of the six are architectural: the product
cannot be built the way the spec requires without them.

| # | Requirement | Why it is hard, not weighted | Acceptable evidence |
| --- | --- | --- | --- |
| H1 | Commercial use is permitted | This is a commercial product. §2.5 makes documented usage rights mandatory. | Terms text |
| H2 | Storage and caching are permitted | §36 requires a market **snapshot** per analysis and §37 forbids calling a provider during a user request. A provider whose terms forbid caching is architecturally incompatible, not merely inconvenient. | Terms text |
| H3 | Derived data is permitted | Expected graded value, `EV = Σ P(g)·V(g)`, transforms provider prices into a new figure shown to users. If derived-data rights are unclear the economic engine's entire output is in question. | Terms text |
| H4 | No clause restricting competing or comparable products | This is the clause that excludes TCGplayer, and it is common. | Terms text |
| H5 | Raw Pokémon prices, English | The incremental grading decision is *graded proceeds − raw-sale opportunity value − incremental costs*. Without a raw price there is no decision to make. | Documentation or observed response |
| H6 | Japanese-card coverage | A V1 scope requirement, not a nice-to-have. **Satisfiable by a two-provider composition** — see below. | Documentation or observed response |

H6 is the one hard requirement a *composition* may satisfy jointly. H1–H4 must
hold for every member of a composition independently; a licence does not
average.

## Weighted preferences

Each is scored **0–3** and multiplied by its weight. Maximum 57.

| # | Criterion | Weight | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | Graded prices — PSA | 3 | none | a few flagship cards | most graded-worthy cards | broad, per grade |
| W2 | Graded prices — BGS | 2 | none | a few flagship cards | most graded-worthy cards | broad, per grade |
| W3 | Graded prices — TAG | 1 | none | a few flagship cards | most graded-worthy cards | broad, per grade |
| W4 | Historical data | 3 | current price only | < 6 months | 6–24 months | > 24 months, queryable |
| W5 | Rate limits against daily refresh | 2 | cannot refresh the catalog daily | needs > 12 h | needs 1–12 h | comfortable headroom |
| W6 | Cost | 2 | beyond the project's means | high | moderate | free or negligible |
| W7 | Redistribution rights | 1 | forbidden | internal only | display permitted with limits | display permitted broadly |
| W8 | Attribution requirements | 1 | onerous or unclear | prominent, prescribed placement | a visible credit | none, or trivial |
| W9 | Reliability | 2 | frequent outages or no track record | unknown | stable, no published SLA | stable with a published SLA |
| W10 | API access and data provenance | 2 | no API; provenance unknown | scraping only | documented API, provenance stated | documented API, sourced from stated marketplaces |

W7 scoring low is survivable and W7 scoring zero is not disqualifying: the
product can compute a recommendation without ever redisplaying a provider's
price verbatim. It is scored because the results UI is better when it can.

**Rate limits (W5) are recorded against the number of cards that must actually
be refreshed**, not in the abstract. Today's catalog is **49,399 cards** (33,780
English, 15,619 Japanese) across 381 sets, and §37 targets a once-per-day
refresh. Record the limit, the derived time to refresh the whole catalog, and
the assumption used (one request per card, or a batch endpoint's batch size).

## Two scoring rules decided in advance

Both of these are decided here, before any candidate is seen, because deciding
them later is exactly the failure this issue exists to prevent.

### TAG coverage is scored, never disqualifying

TAG is far less widely tracked than PSA and BGS, and it is likely to be the
scarcest signal in the whole survey. A candidate with **no** TAG coverage loses
3 points of 57 and remains fully viable.

That is not a shrug. The pipeline's answer for a TAG value it cannot source is
`insufficient_information`, which is a legitimate output — a mediocre answer
with clear uncertainty beats a confidently wrong one. What is *not* acceptable
is substituting a PSA price for a TAG price, or interpolating one, which would
be fabricated certainty in the one place the user is deciding where to spend
money.

### A composition of two providers is scored as one candidate

A candidate that covers raw prices well and graded prices poorly may combine
with its mirror image into something better than either. Compositions are
therefore first-class entries in the register, scored by these rules:

- **H1–H4 must hold for each member independently.** H5 and H6 may be satisfied
  by the union.
- Each weighted criterion takes the **best** member's score — except:
- **W9 (reliability) takes the worst member's score.** A composition is only as
  available as its weakest link, and a daily ingestion that half-fails is a
  snapshot with holes in it.
- **W5 and W6 (rate limits and cost) sum**, then score. Two providers cost two
  subscriptions and consume two rate budgets.
- **A composition is then penalised 3 points.** A second adapter, a second set
  of terms to track, a second failure mode in ingestion and a second thing to
  re-verify are real, permanent operating costs, and a rubric that ignores them
  will always prefer more providers to fewer.

## Manual curation is a candidate, not a failure state

Spec §69/M3 names it as an acceptable V1 outcome: *"V1 launches with a manually
curated provider implementation if no suitable API is commercially usable."*

It is entered in the register and scored like everything else. It trivially
passes H1–H4 and is scored honestly on the rest — in particular on W4
(historical data: a curated set has none at first), W5 (refresh cadence: what a
person can actually maintain daily) and coverage, which for a curated set means
whichever cards were curated, not the catalog.

Score it against a stated scope: how many cards, chosen how, refreshed how
often, by whom.

## Comparison template

One of these per candidate, filled in by #43. Copy it verbatim; a missing row is
a finding, not an omission.

```markdown
### <Candidate name>

- **What it is:** <one line>
- **Status:** shortlisted | eliminated (<reason>)
- **Terms URL / version / date read:** <url> · <version or effective date> · <YYYY-MM-DD>
- **Docs URL / date read:** <url> · <YYYY-MM-DD>
- **Currency of quoted prices:** <e.g. USD> (V1 economics are SGD; recorded, not scored)

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass / fail / unclear | <quote + citation> |
| H2 storage and caching | pass / fail / unclear | |
| H3 derived data | pass / fail / unclear | |
| H4 no competing-product clause | pass / fail / unclear | |
| H5 raw Pokémon prices (EN) | pass / fail / unclear | |
| H6 Japanese coverage | pass / fail / unclear | |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | | 3 | | |
| W2 BGS | | 2 | | |
| W3 TAG | | 1 | | |
| W4 historical data | | 3 | | |
| W5 rate limits | | 2 | | |
| W6 cost | | 2 | | |
| W7 redistribution | | 1 | | |
| W8 attribution | | 1 | | |
| W9 reliability | | 2 | | |
| W10 API and provenance | | 2 | | |
| **Total** | | | **/57** | |

- **Refresh arithmetic:** <limit> → <time to refresh 49,399 cards> (assuming <batching assumption>)
- **Risks and ambiguities:** <recorded, not resolved>
```

## Candidate register

Filled in by #43. Empty by design — the rubric ships before the candidates.

## Licensing determinations

Filled in by #44, for shortlisted candidates only. Each records all eight §78
points — commercial use, derived data, storage, caching, display,
redistribution, attribution, and any competing-product restriction — with the
terms version and the date assessed, plus a plain-language summary of what the
project may and may not do.

These map onto the `market_providers` columns spec §35 defines, so that what the
database stores is traceable to a reading recorded here:

| §35 column | Comes from |
| --- | --- |
| `name` | the candidate's name |
| `version` | the provider's API or data version |
| `license` | the licence or terms name |
| `commercial_use` | the H1 determination |
| `terms_reference` | the terms URL and version recorded above |

## Review trigger

The ADR that follows this research is reviewed when any of these becomes true —
recorded here because they are properties of the rubric, not of the winner:

- the selected provider's terms change
- coverage degrades below a hard requirement
- cost changes materially
- a rate limit stops supporting a once-per-day refresh of the catalog
