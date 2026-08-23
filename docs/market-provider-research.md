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

Surveyed 2026-08-24 by [#43](https://github.com/chuanseng-ng/tcg-analyzer/issues/43).
Every finding below was verified on that date unless a terms document states its
own effective date, which is recorded where it does.

**How eliminated candidates are recorded.** The rubric says to copy the
comparison template verbatim and that *"a missing row is a finding, not an
omission"*. A candidate eliminated at a hard requirement therefore keeps its
full hard-requirement table, and its weighted rows read `n/a` with the
eliminating requirement named, rather than carrying numbers. Scoring the graded
coverage of an API nobody can obtain access to would be fabrication; the row is
present and says why it is empty.

### Scoreboard

| # | Candidate | H1 | H2 | H3 | H4 | H5 | H6 | Score | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **PkmnPrices** | pass\* | unclear | unclear | pass | pass | pass | **45**/57 | **shortlisted** |
| 2 | PkmnPrices + PokemonPriceTracker (composition) | pass\* | unclear | unclear | pass | pass | pass | **41**/57 | live, but scores below its own better member |
| 3 | **PokemonPriceTracker** | pass | **pass** | unclear | pass | pass | pass | **37**/57 | **shortlisted** |
| 4 | **Scrydex** | unclear | unclear | unclear | unclear | pass | pass | **35**/57 | **shortlisted** |
| 5 | Manual curation | pass | pass | pass | pass | pass | pass | **24**/57 | not shortlisted — retained as the §69/M3 fallback |
| 6 | JustTCG | **pass** | **pass** | **pass** | pass | pass | pass | **23**/57 | not shortlisted — eliminated on coverage and refresh capacity, **not** on rights |
| 7 | TCGdex pricing | unclear | unclear | unclear | pass | pass | unclear | not scored | eliminated — upstream rights (H1–H3) |
| 8 | PriceCharting | **fail** | n/a | n/a | n/a | pass | unclear | not scored | eliminated — H1 |
| 9 | TCGplayer API | fail | n/a | n/a | **fail** | pass | unclear | not scored | eliminated — spec §34, no access |
| 10 | Cardmarket API | fail | n/a | n/a | n/a | pass | pass | not scored | eliminated — applications closed |
| 11 | eBay Marketplace Insights | fail | n/a | n/a | n/a | pass | pass | not scored | eliminated — limited release, closed |
| 12 | Scraping a marketplace | **fail** | n/a | n/a | fail | n/a | n/a | not scored | eliminated — H1/H4 as a category |
| 13 | PokeTrace | unclear | unclear | unclear | unclear | unclear | unclear | not scored | eliminated — no locatable API documentation or terms |

\* PkmnPrices' commercial-use permission is stated on its published pricing
page as a per-tier feature, **not** in its terms of service, which are silent on
the subject. Under this document's evidence standard that is a documentation
citation supporting an offer, not terms text granting a right. It is recorded as
`pass\*` and is the first question [#44](https://github.com/chuanseng-ng/tcg-analyzer/issues/44)
must settle.

### PkmnPrices

- **What it is:** a Pokémon card data and price API over TCGplayer, Cardmarket and eBay, covering English, Japanese and German cards, sealed products and graded slabs
- **Status:** shortlisted
- **Terms URL / version / date read:** <https://www.pkmnprices.com/terms> · no version or effective date stated · 2026-08-24
- **Docs URL / date read:** <https://www.pkmnprices.com/developers> · 2026-08-24
- **Currency of quoted prices:** USD (TCGplayer) and EUR (Cardmarket) (V1 economics are SGD; recorded, not scored)

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass\* | "Commercial use" is listed as an included feature of **every** tier, Free included, on the pricing table at <https://www.pkmnprices.com/developers> (2026-08-24). The terms of service do not mention commercial use at all. Documentation, not terms text — see the scoreboard note. |
| H2 storage and caching | **unclear** | The terms are silent. §4 "API Access and Usage" enumerates five prohibitions — exceeding rate limits, sharing keys, unlawful use, reverse-engineering, and scraping outside the API — and says nothing about storing or caching responses. Silence is not permission. |
| H3 derived data | **unclear** | The terms are silent on derivative works or analyses. §7 asserts ownership of "the Service, its design, code, and documentation" and does not address the data. |
| H4 no competing-product clause | pass | No such clause exists. The only redistribution prohibition is on credentials: "Share, redistribute, or resell API keys" (§4). |
| H5 raw Pokémon prices (EN) | pass | "Daily market prices for each condition a card actually sells in", "then again for every printing — normal, reverse holo, first edition" (<https://www.pkmnprices.com/>, 2026-08-24). Sources named on the developer page: TCGplayer, Cardmarket, eBay. |
| H6 Japanese coverage | pass | "Japanese and German set data and pricing available on Pro and Business tiers." "English and Japanese pages carry a full year of price history." (<https://www.pkmnprices.com/developers>, <https://www.pkmnprices.com/>, 2026-08-24) |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 3 | 3 | 9 | "Graded cards are valued from actual eBay sold listings — PSA, BGS, CGC and the rest — not a raw price with a multiplier bolted on." Per-grade figures illustrated (PSA 10, BGS 9.5, CGC 9). |
| W2 BGS | 3 | 2 | 6 | Same source; BGS is named and illustrated per grade. |
| W3 TAG | 2 | 1 | 2 | **TAG is listed explicitly** in the published grade list: "PSA CGC BGS SGC TAG ACE PCA SFG CGS PGS EGS AOG GSG GCG MTG". Scored 2 rather than 3 because per-card TAG depth is undocumented and, on desk research, unverified. |
| W4 historical data | 2 | 3 | 6 | "A year of history, not a snapshot… Every card and sealed product carries a full price history"; charts offered over 7, 30, 90 days and a full year. 6–24 months. |
| W5 rate limits | 3 | 2 | 6 | Business: 200,000 credits/day, 200 requests/minute. Comfortable headroom over a 49,399-card catalog. See refresh arithmetic below. |
| W6 cost | 2 | 2 | 4 | Free $0; Pro $14.99/mo (adds Japanese); Business $89.99/mo. Japanese coverage — a hard requirement — starts at Pro, and the credit budget for a full daily refresh needs Business. |
| W7 redistribution | 1 | 1 | 1 | Terms are silent on redistributing or displaying the data. Scored as internal-only until the silence is resolved; it is not scored 0, because nothing forbids it either. |
| W8 attribution | 3 | 1 | 3 | No attribution obligation is imposed anywhere in the terms. |
| W9 reliability | 1 | 2 | 2 | No published SLA and no established track record. "Built with Rust for sub-100ms responses" is a performance claim, not an availability commitment. |
| W10 API and provenance | 3 | 2 | 6 | Documented REST API, typed TypeScript and Python SDKs, cursor-paginated endpoints, API-key auth. Sources stated by name and logo: TCGplayer, Cardmarket, eBay. |
| **Total** | | | **45/57** | |

- **Refresh arithmetic:** Business = 200,000 credits/day against 49,399 cards. At the conservative assumption of **one request per card**, a full refresh consumes 49,399 credits — 25% of the daily budget — and at 200 requests/minute takes **~4.1 hours**. The developer page documents "Paginated endpoints" and a `per_page` parameter, so a list-based refresh would cost far less; the page size was **not verified** on desk research, and the arithmetic above deliberately assumes no batching. Pro's 20,000 credits/day does **not** cover a one-request-per-card daily refresh.
- **Risks and ambiguities:**
  - **The terms are thin, and that is the finding.** Eleven short sections that address accounts, payment, liability and termination, and grant nothing about the data. The commercial-use permission lives on a pricing page. H2 and H3 — both hard requirements, both architectural — rest on silence.
  - Graded prices derive from eBay sold listings. Whatever rights eBay's own terms impose on that derivation are not addressed here and are not visible from outside.
  - No published effective date on the terms, so the ninety-day re-verification rule has no anchor other than the date read.
  - TAG per-card depth unverified.

### PokemonPriceTracker

- **What it is:** a Pokémon price API over TCGplayer, Cardmarket and eBay completed listings, with PSA graded history and population reports, English and Japanese
- **Status:** shortlisted
- **Terms URL / version / date read:** <https://www.pokemonpricetracker.com/terms> · **effective 2026-08-19** · 2026-08-24
- **Docs URL / date read:** <https://www.pokemonpricetracker.com/pokemon-card-price-api> · 2026-08-24
- **Currency of quoted prices:** USD (TCGplayer, eBay) and EUR (Cardmarket, beta, paid plans)

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass | "Using PokePriceTracker Data for any commercial purpose requires an active Business or Enterprise subscription." (terms, effective 2026-08-19). Corroborated by the pricing page: "Commercial use is allowed exclusively on our Business plan ($99/mo)." |
| H2 storage and caching | **pass** | "You may store and cache PokePriceTracker Data in your own systems and serve it to the end users of your own application." (terms). This is an explicit grant of exactly what §36 and §37 require. |
| H3 derived data | **unclear** | The terms grant storage, caching and service to end users, and prohibit resale of the raw data, but **do not separately define rights to derivative works or analyses**. `EV = Σ P(g)·V(g)` is neither the raw data nor a redistribution of it, and the terms do not say which side of the line it falls on. |
| H4 no competing-product clause | pass | The clause exists but is narrow: "You may not use our API to power your own competing API that sells or provides the same pricing data to third parties." A grading advisor is not a pricing API and does not sell pricing data. |
| H5 raw Pokémon prices (EN) | pass | "daily-updated prices from TCGPlayer, Cardmarket EUR prices for the European market (Beta, paid plans), historical price trends" (docs). |
| H6 Japanese coverage | pass | "fully supports both English and Japanese Pokemon cards. You can filter by language using the language parameter in your API requests." (docs). |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 2 | 3 | 6 | "We provide historical sales data for PSA 8, 9, and 10 grades sourced from eBay completed listings." Three grades, not the full scale — most graded-worthy cards, not broad per grade. |
| W2 BGS | 1 | 2 | 2 | "PSA, CGC, BGS graded prices for English & Japanese cards with population report data" is claimed on the marketing copy, but the FAQ details PSA only and names no BGS grades. Scored on what is documented, not on what is claimed. |
| W3 TAG | 0 | 1 | 0 | TAG appears nowhere in the documentation. |
| W4 historical data | 2 | 3 | 6 | Free 3 days; API tier 6 months; Business tier 12+ months. |
| W5 rate limits | 3 | 2 | 6 | Business: 200,000 credits/day, **500 requests/minute** — the highest published request rate of any candidate. |
| W6 cost | 2 | 2 | 4 | Free $0; API $9.99/mo; Business $99/mo. Commercial use requires Business, so $99/mo is the real price. |
| W7 redistribution | 2 | 1 | 2 | "serve it to the end users of your own application" is permitted; "resell, sublicense, syndicate, or redistribute the raw data itself as a standalone product or data service" is not. Display permitted with limits. |
| W8 attribution | 3 | 1 | 3 | No attribution requirement stated in the terms. |
| W9 reliability | 1 | 2 | 2 | No published SLA. |
| W10 API and provenance | 3 | 2 | 6 | Documented REST API with an API reference; provenance stated — TCGplayer, Cardmarket, and eBay completed listings named as the sources of each figure. |
| **Total** | | | **37/57** | |

- **Refresh arithmetic:** Business = 200,000 credits/day, 500 requests/minute. One request per card over 49,399 cards is **~99 minutes** and 25% of the daily credit budget. Comfortable, and the fastest of any candidate. Batching not assumed.
- **Risks and ambiguities:**
  - **H3 is the one open hard requirement**, and it is the one that reaches furthest: the economic engine's entire output is derived data.
  - The terms took effect **2026-08-19, five days before this reading**. Recently rewritten terms are more likely to be revised again; the review trigger matters here.
  - BGS coverage is claimed in marketing and undocumented in the reference. Treat W2 as provisional.
  - The product name is inconsistent between the site (PokemonPriceTracker) and the terms document (PokePriceTracker). Recorded because a licensing determination must name the right legal entity.

### Scrydex

- **What it is:** a paid multi-TCG API with raw and graded prices, population reports, price history and image analysis; widely reported as the successor to the free pokemontcg.io
- **Status:** shortlisted
- **Terms URL / version / date read:** <https://scrydex.com/terms> · no version or effective date stated · 2026-08-24
- **Docs URL / date read:** <https://scrydex.com/pricing>, <https://scrydex.com/docs/getting-started/best-practices>, <https://scrydex.com/faq> · 2026-08-24
- **Currency of quoted prices:** USD

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **unclear** | The terms contemplate commercial use — "You must be at least 18 years old… to use the Services for commercial purposes" — while also prohibiting anyone who would "commercially exploit the Services without prior written authorization from Scrydex". The two are not reconciled in the text, and "commercially exploit" is broad enough to reach an ordinary paid integration. |
| H2 storage and caching | **unclear** | The **terms** contain no caching or storage clause. The **documentation** actively recommends it: "Caching API responses locally allows you to reuse data without making repeated API calls", with Redis or PostgreSQL suggested and "historical price data for charts" named as a thing to persist. Documentation is not a grant of rights. |
| H3 derived data | **unclear** | Not addressed anywhere in the terms. |
| H4 no competing-product clause | **unclear — a clause exists** | Prohibited: "Use the Services primarily as a substitute backend, proxy, or wholesale data source for a competing commercial product or service without written authorization." The qualifier *primarily … substitute backend, proxy, or wholesale data source* suggests it targets resale rather than consumption, and a grading advisor does not compete with a TCG data API — but this is the family of clause that excludes TCGplayer, and it is not resolved by reading. |
| H5 raw Pokémon prices (EN) | pass | "Raw Prices" listed as an included feature of every tier (<https://scrydex.com/pricing>, 2026-08-24). |
| H6 Japanese coverage | pass | "comprehensive data for both English and Japanese expansions… scope their requests to a specific language by introducing a language code in the URL structure" (documentation). |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 3 | 3 | 9 | "real-time market values, price history, and trends across raw and graded cards including PSA, BGS, CGC, TAG, ACE & more"; population reports listed as a tier feature. |
| W2 BGS | 3 | 2 | 6 | Same source; BGS named. |
| W3 TAG | 2 | 1 | 2 | **TAG named explicitly** in the graded coverage list. Scored 2 rather than 3 on the same grounds as PkmnPrices: per-card depth undocumented. |
| W4 historical data | 2 | 3 | 6 | "Price History" is a listed feature (3 credits per request) and historical sold data across eBay and auction houses is offered for graded cards. Depth in months is **not published**. |
| W5 rate limits | **0** | 2 | 0 | Credits are allocated **per month**, not per day. Professional, at $399/mo, provides 250,000 credits ≈ 8,300 per day against a 49,399-card catalog. **No published tier supports a once-per-day refresh.** See below. |
| W6 cost | 1 | 2 | 2 | Starter $29, Growth $99, Professional $399, Enterprise custom. The tier that would actually support daily ingestion is Enterprise, whose price is unpublished — recorded as *unpublished*, which is a finding. |
| W7 redistribution | 1 | 1 | 1 | "Resell, sublicense, redistribute, mirror, or commercially exploit the Services without prior written authorization" is prohibited; display to end users is not addressed. |
| W8 attribution | 3 | 1 | 3 | No attribution obligation in the terms. Images carry a separate acknowledgement — "Scrydex does not claim ownership of the images provided by the API" — which does not apply here: this project displays no catalog images at all (ADR 0004). |
| W9 reliability | 1 | 2 | 2 | No published SLA. The pokemontcg.io lineage is a third-party claim (CardGrader, 2026), cited as context, not as evidence. |
| W10 API and provenance | 2 | 2 | 4 | Documented API with a published reference and best-practices guide. Provenance of the price figures — which marketplaces, which sales — is **not stated** on any page read. |
| **Total** | | | **35/57** | |

- **Refresh arithmetic:** 49,399 cards refreshed daily is **1,481,970 credit-consuming requests per month** at one request per card. Professional provides 250,000/month — **17% of what a daily refresh needs**. Even the Enterprise floor of "1,000,000+" credits falls short at that assumption. A batch or bulk endpoint would change this entirely and none is documented; the assumption is stated rather than hidden because it is what drives W5 to zero.
- **Risks and ambiguities:**
  - **The pricing page and the FAQ contradict each other on graded data.** The pricing page lists "Graded Prices" as included in all four tiers; the FAQ says graded prices arrive "once you upgrade to a plan that includes graded prices (Growth or Professional)". Both are the provider's own documentation. Unresolved, and it moves the real entry price between $29 and $99.
  - Every one of H1–H4 is unclear. Scrydex is shortlisted for its coverage — it is one of only two candidates that name TAG — and #44 will need written clarification rather than a closer reading, because the text does not contain the answers.
  - No effective date on the terms.

### PkmnPrices + PokemonPriceTracker — composition

- **What it is:** PkmnPrices for breadth of graded coverage including TAG; PokemonPriceTracker for its explicit caching grant, PSA history and the highest request rate
- **Status:** live, but it scores below PkmnPrices alone
- **Terms / docs:** as recorded for each member above · 2026-08-24
- **Currency of quoted prices:** USD and EUR

Hard requirements, applied per member as the rubric requires — H1–H4 must hold
for each independently, H5 and H6 may be satisfied by the union:

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass\* | PokemonPriceTracker passes on terms text; PkmnPrices passes on a pricing page only. **A licence does not average**, so the composition inherits the weaker reading. |
| H2 storage and caching | **unclear** | PokemonPriceTracker grants it explicitly; PkmnPrices is silent. The composition is unclear because the silent member is still in it. |
| H3 derived data | **unclear** | Neither member addresses it. |
| H4 no competing-product clause | pass | Both pass — PokemonPriceTracker's clause is narrow, PkmnPrices has none. |
| H5 raw Pokémon prices (EN) | pass | Either member alone. |
| H6 Japanese coverage | pass | Either member alone. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 3 (best) | 3 | 9 | PkmnPrices |
| W2 BGS | 3 (best) | 2 | 6 | PkmnPrices |
| W3 TAG | 2 (best) | 1 | 2 | PkmnPrices |
| W4 historical data | 2 (best) | 3 | 6 | Both reach 6–24 months |
| W5 rate limits | 3 (summed) | 2 | 6 | Two Business tiers, two full refreshes; the slower member (200 req/min) binds at ~4.1 h |
| W6 cost | 1 (summed) | 2 | 2 | $89.99 + $99 = **$188.99/month** |
| W7 redistribution | 2 (best) | 1 | 2 | PokemonPriceTracker |
| W8 attribution | 3 (best) | 1 | 3 | Neither imposes one |
| W9 reliability | 1 (**worst**) | 2 | 2 | Both score 1; no SLA either side |
| W10 API and provenance | 3 (best) | 2 | 6 | Both document their sources |
| Subtotal | | | 44 | |
| Composition penalty | | | **−3** | Second adapter, second set of terms to track, second ingestion failure mode |
| **Total** | | | **41/57** | |

- **Refresh arithmetic:** two independent refreshes of 49,399 cards. Both members hold 200,000 credits/day, so each refresh costs a quarter of its own budget; wall-clock is set by the slower rate limit at ~4.1 hours.
- **Risks and ambiguities:**
  - **A composition cannot launder rights, and this is the clearest lesson of the survey.** The reason to reach for a second provider is usually coverage; here the temptation is to pair a provider with good terms against one with good data. The rubric forbids it — H1–H4 hold per member — and it is right to: JustTCG's permission to compute derived metrics grants nothing whatsoever over PkmnPrices' figures.
  - The composition costs **twice the money and −3 points** to buy PokemonPriceTracker's explicit caching grant on top of PkmnPrices' coverage. If #44 resolves PkmnPrices' H2 and H3 favourably, the composition has no remaining purpose.

### JustTCG

- **What it is:** a condition-specific TCG pricing API blending marketplace data with in-store sales from a partner network of local game stores
- **Status:** not shortlisted — eliminated on coverage and refresh capacity, **not** on rights
- **Terms URL / version / date read:** <https://justtcg.com/terms> · **effective 2026-07-27** · 2026-08-24
- **Docs URL / date read:** <https://justtcg.com/> · 2026-08-24
- **Currency of quoted prices:** multi-region; "genuine multi-region pricing" claimed for graded variants

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass** | "Display current prices, historical trends, and percentage changes to end users within a consumer-facing application or website" is an enumerated permitted use on paid tiers. The free tier is "restricted to personal, non-commercial use only". |
| H2 storage and caching | **pass** | "Cache API responses server-side and store historical price points for as long as your subscription remains active, strictly to support features, logic, and user histories within your own application." |
| H3 derived data | **pass** | "Calculate and display derived metrics, market observations, and aggregate valuations based on data obtained from the Service." This is the only candidate whose terms describe `EV = Σ P(g)·V(g)` as a permitted act. The terms additionally permit "Combine data obtained from the Service with other lawfully obtained market data sources" — an explicit blessing of the composition pattern. |
| H4 no competing-product clause | pass | The clause is narrow and does not reach this product: "Build, train, or operate a pricing API or other product that serves as a substitute for or competitor to the Service." |
| H5 raw Pokémon prices (EN) | pass | Condition-specific real-time pricing across variants, foils, alt arts and promos. |
| H6 Japanese coverage | pass | "Pokémon Japan" is listed as a distinct supported game. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 1 | 3 | 3 | "Graded card support **BETA**"; PSA, BGS and CGC treated as "first-class variants" in a v2 beta. Beta coverage of unstated breadth. |
| W2 BGS | 1 | 2 | 2 | Same beta. |
| W3 TAG | 0 | 1 | 0 | Not offered. |
| W4 historical data | **0** | 3 | 0 | "Historical pricing data being collected daily (30d and 90d access coming soon)." Collection has begun; access has not. Today the API answers with the current price. |
| W5 rate limits | **0** | 2 | 0 | Enterprise, the largest published tier, is 500,000 calls/month ≈ 16,666/day against 49,399 cards. No published tier supports a daily catalog refresh. |
| W6 cost | 2 | 2 | 4 | Free (non-commercial); Starter $19/mo; Professional $49/mo; Enterprise $149/mo+. |
| W7 redistribution | 3 | 1 | 3 | Display to end users explicitly permitted; only standalone feeds and bulk datasets are forbidden. |
| W8 attribution | 3 | 1 | 3 | "Attribution to JustTCG is appreciated but not required for internal or commercial use on paid tiers." |
| W9 reliability | 1 | 2 | 2 | No published SLA. |
| W10 API and provenance | 3 | 2 | 6 | Documented API; **provenance is the best stated of any candidate** — online marketplace data plus real sales from "45+ verified local game stores" via a partner programme, aggregated as volume-weighted averages. |
| **Total** | | | **23/57** | |

- **Refresh arithmetic:** 49,399 cards daily = 1,481,970 calls/month at one call per card, against Enterprise's published 500,000 — **34% of what is needed**. Batch endpoints undocumented; no batching assumed.
- **Risks and ambiguities:** none material to the rights. The eliminations are factual: 0 on historical data and 0 on refresh capacity together remove 15 of the 57 available points, and no reweighting inside the rubric recovers them.
- **Why this entry matters even though it lost.** JustTCG is the only candidate whose terms **explicitly grant all three architectural hard requirements** — caching, storage and derived data — in language written for exactly this use. It is the reference text for what an unambiguous grant looks like, and #44 should put language of that shape in front of the shortlisted candidates rather than inventing its own. Its low score is a coverage and capacity verdict, and if graded coverage leaves beta and history reaches 12 months, it re-enters immediately.

### Manual curation

- **What it is:** a hand-maintained price table inside this repository, refreshed by a person from public sold listings — the outcome spec §69/M3 names as acceptable
- **Status:** not shortlisted, and **not eliminated** — retained as the documented fallback
- **Terms URL / version / date read:** not applicable; the data is the project's own recording of public sale outcomes
- **Docs URL / date read:** not applicable
- **Currency of quoted prices:** whatever the curator records (SGD directly, if wanted — the only candidate that can)

Scored against a **stated scope**, because "manual curation" without one is not a
candidate: **500 cards**, chosen as the intersection of high catalog traffic and
plausible grading candidacy, across English and Japanese, refreshed **weekly**
by one person, recording raw plus PSA 9 and PSA 10 from completed public sales.

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass | The project records the figures itself; no third party grants or withholds the right. |
| H2 storage and caching | pass | Same. |
| H3 derived data | pass | Same. |
| H4 no competing-product clause | pass | None exists. |
| H5 raw Pokémon prices (EN) | pass | Within the curated 500. |
| H6 Japanese coverage | pass | Within the curated 500. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | 1 | 3 | 3 | 500 cards of 49,399 — "a few flagship cards" is the honest band, whatever effort goes in. |
| W2 BGS | 1 | 2 | 2 | Same, and BGS sales are thinner, so the same effort yields fewer figures. |
| W3 TAG | 1 | 1 | 1 | A person can read a TAG sold listing as readily as a PSA one. This is the only candidate whose TAG coverage is limited by sales volume rather than by the provider. |
| W4 historical data | **0** | 3 | 0 | None on day one, and it accrues only at the rate it is recorded. |
| W5 rate limits | **0** | 2 | 0 | The constraint is a person, not an API. 49,399 cards cannot be refreshed daily by hand, and weekly for 500 is already a standing commitment. |
| W6 cost | 3 | 2 | 6 | No subscription. The labour cost is real and is charged against W5, not counted twice here. |
| W7 redistribution | 3 | 1 | 3 | Unrestricted — it is the project's own record. |
| W8 attribution | 3 | 1 | 3 | None owed. |
| W9 reliability | 2 | 2 | 4 | No outage, no rate limit, no provider that can change its terms — but no track record either, and it fails silently when the person is unavailable. |
| W10 API and provenance | 1 | 2 | 2 | No API. Provenance is *better* than any candidate's — each figure names the sale it came from — but the rubric's band for "no API" caps this at 1. Recorded as a place the rubric under-describes the option rather than adjusted. |
| **Total** | | | **24/57** | |

- **Refresh arithmetic:** not rate-limited; **effort-limited**. 500 cards weekly is roughly 70 lookups a day, indefinitely. Extending to 49,399 cards is not a matter of more effort — it is four orders of magnitude out.
- **Risks and ambiguities:**
  - **It outscores JustTCG.** That is not an argument for it; it is the rubric reporting that a real API with no history and no refresh headroom is worth about as much as a small hand-kept table. Worth saying out loud so nobody reads 24/57 as a wooden spoon.
  - A user's card outside the curated 500 gets `insufficient_information` — a legitimate output, but it is the modal outcome here, not the exception.
  - It is the only candidate immune to the review trigger, because nobody else's terms can change under it.

### TCGdex pricing

- **What it is:** this project's existing catalog source (ADR 0004), which also proxies Cardmarket and TCGplayer prices in its card responses
- **Status:** eliminated — upstream rights (H1–H3)
- **Terms URL / version / date read:** <https://github.com/tcgdex/cards-database> (MIT) · 2026-08-24. No separate terms govern the price data.
- **Docs URL / date read:** <https://tcgdex.dev/markets-prices> · 2026-08-24
- **Currency of quoted prices:** EUR (Cardmarket) and USD (TCGplayer)

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **unclear, leaning fail** | "The Database is licensed under the MIT License" covers TCGdex's **compilation** — the same finding ADR 0004 already made when it declined to import catalog images. TCGdex cannot grant rights it does not hold over Cardmarket's and TCGplayer's price data, and the documentation is silent on the point. |
| H2 storage and caching | unclear | Not addressed anywhere. |
| H3 derived data | unclear | Not addressed anywhere. |
| H4 no competing-product clause | pass | MIT imposes none. |
| H5 raw Pokémon prices (EN) | pass | Cardmarket "current averages, trends, lows, and 7/30-day historical data"; TCGplayer "low, mid, high, market, and direct pricing". |
| H6 Japanese coverage | **unclear** | TCGdex covers Japanese *cards* — this project already imports 15,619 of them — but the pricing documentation names only Cardmarket and TCGplayer, neither of which prices Japanese printings comprehensively, and says "If the card is not listed on a marketplace, the provider will be omitted." Japanese *price* coverage is undocumented and unverified. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | n/a | 3 | n/a | eliminated at H1 — and no graded prices are offered at all |
| W2 BGS | n/a | 2 | n/a | eliminated at H1 — none offered |
| W3 TAG | n/a | 1 | n/a | eliminated at H1 — none offered |
| W4 historical data | n/a | 3 | n/a | eliminated at H1 (1/7/30-day averages only, in any case) |
| W5 rate limits | n/a | 2 | n/a | eliminated at H1 — no rate limits documented |
| W6 cost | n/a | 2 | n/a | eliminated at H1 — free, and the adapter already exists |
| W7 redistribution | n/a | 1 | n/a | eliminated at H1 |
| W8 attribution | n/a | 1 | n/a | eliminated at H1 |
| W9 reliability | n/a | 2 | n/a | eliminated at H1 |
| W10 API and provenance | n/a | 2 | n/a | eliminated at H1 — provenance *is* stated (Cardmarket, TCGplayer), which is precisely how the upstream problem became visible |
| **Total** | | | **not scored** | eliminated at H1 |

- **Refresh arithmetic:** not computed — no rate limits are documented and the candidate does not reach scoring.
- **Risks and ambiguities:** the temptation here was real and worth naming. TCGdex is **already integrated**, already trusted, and its prices arrive free inside responses this project may already be making — the cheapest possible adapter. It fails anyway, for the same reason ADR 0004 refused its card images: an MIT licence over a compilation is not a licence over what the compilation points at. Cardmarket's own terms are the confirmation — see below.

### PriceCharting

- **What it is:** a long-established price guide that prices graded slabs (PSA, BGS, CGC) separately from raw copies, with an API
- **Status:** **eliminated — H1**
- **Terms URL / version / date read:** <https://www.pricecharting.com/page/terms-of-service> · **no version or effective date stated** · 2026-08-24
- **Docs URL / date read:** <https://www.pricecharting.com/api-documentation> · 2026-08-24
- **Currency of quoted prices:** USD

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | "Price Data cannot be used in any software, application, or system that is accessible to third parties, including customers, clients, or the general public, without express written permission." The terms' own plain-language summary repeats it: "Apps and other software cannot share the price data without express written permission." This product is a system accessible to the general public. |
| H2 storage and caching | n/a | eliminated at H1. For the record: "Price Data can be used for your Internal Business Purposes if you maintain a valid and current Legendary subscription. 'Internal business purposes' refers to usage by the subscriber and their authorized employees or contractors, strictly within the organization, and not for external display or redistribution." |
| H3 derived data | n/a | eliminated at H1 |
| H4 no competing-product clause | n/a | eliminated at H1. There is no competing-product clause as such; the third-party-access prohibition does the same work more broadly. |
| H5 raw Pokémon prices (EN) | pass | Raw and graded prices are the product. |
| H6 Japanese coverage | unclear | Not established — the candidate did not survive to the coverage screen. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 PSA | n/a | 3 | n/a | eliminated at H1 |
| W2 BGS | n/a | 2 | n/a | eliminated at H1 |
| W3 TAG | n/a | 1 | n/a | eliminated at H1 |
| W4 historical data | n/a | 3 | n/a | eliminated at H1 |
| W5 rate limits | n/a | 2 | n/a | eliminated at H1 |
| W6 cost | n/a | 2 | n/a | eliminated at H1 — a "Legendary" subscription is required for even internal use |
| W7 redistribution | n/a | 1 | n/a | eliminated at H1 — external display is prohibited outright |
| W8 attribution | n/a | 1 | n/a | eliminated at H1. For reference, external *websites* may reference the data if "PriceCharting is clearly cited as the source, and a visible hyperlink to PriceCharting is included on the external site" — a permission for citation, not for a product. |
| W9 reliability | n/a | 2 | n/a | eliminated at H1 |
| W10 API and provenance | n/a | 2 | n/a | eliminated at H1 |
| **Total** | | | **not scored** | eliminated at H1 |

- **Refresh arithmetic:** not computed. A widely repeated figure of one API call per second appears in secondary sources; **not verified against the provider's own documentation**, which returned 403 to automated reads, and not relied upon.
- **Risks and ambiguities:** the elimination is unambiguous and does not need #44. "Express written permission" is a door rather than a wall, but pursuing it is a commercial negotiation with an unpublished outcome, and the rubric does not score doors. Recorded so that #45 can note it was considered and declined rather than missed.

### TCGplayer API

- **What it is:** the North American marketplace whose prices most of this survey's candidates resell
- **Status:** **eliminated — spec §34**
- **Terms URL / version / date read:** not read; the candidate is excluded before its terms matter
- **Docs URL / date read:** not read
- **Currency of quoted prices:** USD

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | fail | Spec §34, verbatim: "Current TCGplayer documentation states that new API access is not currently being granted, and its API terms impose restrictions relevant to a competing commercial product." Access that is not granted cannot be licensed. |
| H2 storage and caching | n/a | eliminated |
| H3 derived data | n/a | eliminated |
| H4 no competing-product clause | **fail** | Spec §34, same sentence. |
| H5 raw Pokémon prices (EN) | pass | Not in question. |
| H6 Japanese coverage | unclear | Not established. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | **not scored** | eliminated at H1 and H4 |
| **Total** | | | **not scored** | |

- **Refresh arithmetic:** not computed.
- **Risks and ambiguities:** it is entered here so the register records *why* it is absent rather than leaving a reader to wonder. Spec §34 instructs that the product must not be designed around it, and nothing in this survey does. Note that **four shortlisted or scored candidates resell TCGplayer prices**, which is a different question — this project would hold a licence from its provider, not from TCGplayer — and it is the reason W10's provenance criterion exists.

### Cardmarket API

- **What it is:** the European marketplace of record, with strong Japanese-card presence
- **Status:** **eliminated — applications closed**
- **Terms URL / version / date read:** <https://help.cardmarket.com/en/cardmarket-api> · 2026-08-24
- **Docs URL / date read:** same
- **Currency of quoted prices:** EUR

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | fail | "**Currently, we are not accepting applications for access to the Cardmarket API**" (emphasis in the original, read 2026-08-24). As with TCGplayer, access that cannot be obtained cannot be licensed. The General Terms and Conditions additionally require prior written agreement for "the presentation of the trading cards and their respective prices". |
| H2–H4 | n/a | eliminated |
| H5 raw Pokémon prices (EN) | pass | Not in question. |
| H6 Japanese coverage | pass | Not in question. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | **not scored** | eliminated at H1 |
| **Total** | | | **not scored** | |

- **Refresh arithmetic:** not computed.
- **Risks and ambiguities:** Cardmarket prices still reach this project indirectly, through PkmnPrices and PokemonPriceTracker, both of which name Cardmarket as a source. The rights question there is the provider's to hold, not this project's — which is exactly why H1–H4 are assessed against the provider's terms and W10 records whose data is being resold.

### eBay Marketplace Insights

- **What it is:** eBay's official completed-sales API, and the ultimate source of most graded-card comparables in this survey
- **Status:** **eliminated — limited release, closed to new applicants**
- **Terms URL / version / date read:** not read; access is the blocker
- **Docs URL / date read:** eBay developer documentation and developer-community threads · 2026-08-24
- **Currency of quoted prices:** USD and others by marketplace

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | fail | A Limited Release API gated behind eBay Business approval, reported across 2026 developer-community threads as not accepting new applicants. The predecessor Finding API's `findCompletedItems` was restricted in 2020 and the API was decommissioned 2025-02-04. |
| H2–H4 | n/a | eliminated |
| H5 raw Pokémon prices (EN) | pass | Completed sales cover both raw and graded listings. |
| H6 Japanese coverage | pass | Japanese cards sell on eBay. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | **not scored** | eliminated at H1 |
| **Total** | | | **not scored** | |

- **Refresh arithmetic:** not computed. Note for the ADR: the public completed-sales window is roughly 90 days, which caps how much history *any* eBay-derived candidate can hold going backwards — PkmnPrices' and PokemonPriceTracker's year of history is accumulated forward, not queryable back.
- **Risks and ambiguities:** **every shortlisted candidate's graded prices derive from eBay sold listings.** The shortlist is therefore three ways of buying access to one underlying signal, from parties who each carry their own arrangement with eBay. That is a concentration risk for #45 to state, not a disqualifier.

### Scraping a marketplace

- **What it is:** the category — collecting prices from eBay, TCGplayer, Cardmarket or Yahoo Auctions by crawling rather than by API
- **Status:** **eliminated — H1 and H4, as a category**
- **Terms URL / version / date read:** not applicable; the finding is categorical
- **Docs URL / date read:** not applicable
- **Currency of quoted prices:** not applicable

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | Every marketplace surveyed prohibits it. Two examples already read for other entries: PkmnPrices' own terms forbid "Scrape, crawl, or collect data from the Service outside of the provided API", and Cardmarket's GTC require prior written agreement for the presentation of prices. |
| H2 storage and caching | n/a | eliminated |
| H3 derived data | n/a | eliminated |
| H4 no competing-product clause | fail | Scraping to build a commercial product is the paradigm case these clauses exist to prohibit. |
| H5 raw Pokémon prices (EN) | n/a | eliminated |
| H6 Japanese coverage | n/a | eliminated |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | **not scored** | eliminated at H1 and H4 |
| **Total** | | | **not scored** | |

- **Refresh arithmetic:** not computed.
- **Risks and ambiguities:** entered so the register shows the option was considered and rejected on rights rather than silently skipped. It would score well on coverage, which is exactly why the rubric puts licensing in the hard requirements and not in the weights.

### PokeTrace

- **What it is:** a Pokémon price site advertising a free price API with PSA 10/9/8 values across 60,000+ graded cards
- **Status:** **eliminated — no locatable API documentation or terms**
- **Terms URL / version / date read:** none found · 2026-08-24
- **Docs URL / date read:** <https://poketrace.com/psa-graded-prices> · 2026-08-24 — a consumer-facing guide page, not API documentation
- **Currency of quoted prices:** USD

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | unclear | No terms document could be located. |
| H2 storage and caching | unclear | Same. |
| H3 derived data | unclear | Same. |
| H4 no competing-product clause | unclear | Same. |
| H5 raw Pokémon prices (EN) | unclear | The page read is editorial — grade-multiplier tables and headline card values ("PSA 10 \| 10-100x") with the disclaimer "Prices based on recent verified sales. Actual prices may vary". No endpoint, schema or rate limit is documented. |
| H6 Japanese coverage | unclear | Not addressed. |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | **not scored** | eliminated — six unclear hard requirements and no documentation to resolve them |
| **Total** | | | **not scored** | |

- **Refresh arithmetic:** not computed.
- **Risks and ambiguities:** the elimination is an *evidence* elimination, not a *rights* one — the rubric's standard cannot be met against a candidate that publishes neither documentation nor terms. If PokeTrace publishes both, it re-enters. Recorded this way rather than as a failure so the distinction survives to #45.

## Shortlist

**PkmnPrices (45/57), PokemonPriceTracker (37/57) and Scrydex (35/57)**, in that
order. The **PkmnPrices + PokemonPriceTracker composition (41/57)** is live and
needs no extra licensing work, since #44 covers both its members anyway.

One sentence each on what put it there:

- **PkmnPrices** — the widest coverage in the survey by a clear margin: TAG named alongside PSA, BGS and twelve other graders, a full year of history for English *and* Japanese, graded figures taken from actual eBay sold listings rather than a multiplier on a raw price, and a credit budget with four times the headroom a daily refresh needs, for $89.99 a month.
- **PokemonPriceTracker** — the only candidate whose terms **explicitly permit storing, caching and serving the data to end users of this application**, which is the hard requirement §36 and §37 turn on, at the highest published request rate of any candidate.
- **Scrydex** — one of only two candidates that names TAG, and it adds population reports; it is shortlisted despite four unclear hard requirements and a published rate budget that cannot support a daily refresh, because those are questions #44 can put to the provider whereas coverage a provider does not have cannot be asked for.

### Coverage against the acceptance criterion

Raw, PSA, TAG and BGS, in English and Japanese, per shortlisted candidate.
Documented coverage only; **nothing here was verified by an API call** — this
survey was desk research, and a claim in a provider's documentation is what it
is.

| | PkmnPrices | PokemonPriceTracker | Scrydex |
| --- | --- | --- | --- |
| **Raw — English** | yes, per condition and per printing | yes | yes |
| **Raw — Japanese** | yes, Pro tier and above | yes, `language` parameter | yes, language code in the URL |
| **PSA — English** | yes, from eBay sold listings | yes, PSA 8/9/10 only | yes |
| **PSA — Japanese** | yes — "English and Japanese pages carry a full year of price history" | yes, claimed for both languages | yes |
| **BGS — English** | yes, per grade | **claimed, undocumented** | yes |
| **BGS — Japanese** | yes, implied by the same language coverage | claimed, undocumented | yes |
| **TAG — English** | **yes, named** | **no** | **yes, named** |
| **TAG — Japanese** | undocumented | no | undocumented |

**TAG is the scarcest signal, exactly as the rubric predicted**, and TAG for
Japanese cards is undocumented everywhere. That is not a blocker: it costs 1 or
2 points of 57, and the pipeline's answer for a TAG value it cannot source is
`insufficient_information`. **A PSA price must never be substituted for a TAG
price, and a TAG value must never be interpolated from one** — that would be
fabricated certainty in the one place a user is deciding where to spend money.

### Elimination reasons — the full record

| Candidate | Eliminated because |
| --- | --- |
| JustTCG | Not eliminated on rights — its terms are the best in the survey. **0 on historical data** (collection begun, access "coming soon") and **0 on refresh capacity** (500,000 calls/month against a need of ~1.48 M) remove 15 of 57 points. Re-enters if graded coverage leaves beta and history becomes queryable. |
| Manual curation | Not eliminated. **Retained as the §69/M3 fallback** if #44 finds no shortlisted candidate's rights adequate. 24/57 against a stated scope of 500 cards refreshed weekly. |
| TCGdex pricing | H1–H3 fail upstream. MIT licenses TCGdex's compilation, not Cardmarket's or TCGplayer's prices — the same finding ADR 0004 made about catalog images. Also offers no graded prices at all. |
| PriceCharting | **H1.** "Price Data cannot be used in any software, application, or system that is accessible to third parties… without express written permission." |
| TCGplayer API | **H1 and H4**, per spec §34. New access not granted; terms restrict competing commercial products. |
| Cardmarket API | **H1.** "Currently, we are not accepting applications for access to the Cardmarket API." |
| eBay Marketplace Insights | **H1.** Limited Release, gated behind Business approval and closed to new applicants; the predecessor Finding API was decommissioned 2025-02-04. |
| Scraping a marketplace | **H1 and H4**, categorically. Every marketplace surveyed prohibits it. |
| PokeTrace | Evidence, not rights: neither API documentation nor terms could be located, so no hard requirement can be assessed. Re-enters if both are published. |

### What the survey found that the rubric did not anticipate

Three things, recorded because #45 will need them and because a survey that
reports only scores has thrown away most of what it learned.

1. **A composition cannot launder rights.** The obvious move — pair a provider with excellent terms against one with excellent data — is forbidden by the rubric's own rule that H1–H4 hold per member, and the rule is right. JustTCG's explicit permission to "calculate and display derived metrics" grants nothing whatsoever over PkmnPrices' figures. The only compositions worth scoring are ones assembled for **coverage**, and the one live composition here costs twice the money and 3 points to buy a caching grant that #44 may obtain from PkmnPrices for free.
2. **Silence is the dominant failure mode, not prohibition.** Not one shortlisted candidate forbids what this product needs. Two of the three simply do not address caching, storage or derived data at all, and the rubric's instruction — that ambiguity is recorded as ambiguity and never resolved in the project's favour — is doing almost all of the work. The candidate with the *worst* documentation of rights has the *best* data.
3. **The shortlist is three routes to one signal.** Every graded price in it derives from eBay sold listings, and eBay's own API is closed. The concentration is real: a change in what eBay exposes propagates to all three at once, and no amount of provider diversity in the register mitigates it.

### Handoff to #44

Per shortlisted candidate: the terms document to read, and what is already open.

**PkmnPrices** — <https://www.pkmnprices.com/terms> (no effective date; eleven
sections; the full text is short enough to read in one sitting). Open, and in
priority order:

1. **H2 storage and caching** — silent. The §36 snapshot and §37's prohibition on calling a provider during a user request both depend on the answer.
2. **H3 derived data** — silent. `EV = Σ P(g)·V(g)` is the product's central output.
3. **H1** — commercial use is offered on a pricing page and absent from the terms. Determine whether the pricing page forms part of the agreement.
4. Display and redistribution rights, for the results UI (W7 scored 1 on silence).
5. Whatever eBay's terms impose on the graded figures, which is not visible from outside.

**PokemonPriceTracker** — <https://www.pokemonpricetracker.com/terms>, effective
**2026-08-19**. Read the version in force at the time; it was five days old when
surveyed and a recently rewritten document is likely to move again. Open:

1. **H3 derived data** — the single remaining hard requirement. Storage, caching and service to end users are already granted in terms; derivative works are simply not addressed.
2. Whether "Business or Enterprise" is one tier or two, and what Enterprise costs.
3. Whether BGS coverage exists, since it is claimed in marketing and absent from the reference.

**Scrydex** — <https://scrydex.com/terms> (no effective date). All four of
H1–H4 are open, and **the text does not contain the answers** — this needs
written clarification, not a closer reading:

1. Whether "commercially exploit the Services" reaches an ordinary paid integration that displays derived figures to end users.
2. Whether the caching the documentation recommends is permitted by the terms that omit it.
3. Whether the "substitute backend, proxy, or wholesale data source for a competing commercial product" clause reaches a grading advisor.
4. Derived data, unaddressed.
5. Separately, and not a licensing question: the pricing page and the FAQ contradict each other on whether graded prices are included at the $29 tier, and no published tier supports a daily refresh of 49,399 cards. Enterprise pricing is unpublished.

**The calendar risk, surfaced now as the rubric requires.** No shortlisted
candidate publishes terms behind a paid signup, and all three terms documents
were readable without an account — so the milestone is **not** blocked on
gaining access to a document. The risk has moved instead: **two of the three
shortlisted candidates cannot be resolved by reading at all**, because their
terms are silent on the hard requirements rather than adverse. #44 will need a
written answer from PkmnPrices and from Scrydex, which means a human sending
email and waiting on a reply from a small vendor. That is the path most likely
to slip, and starting it early costs nothing.

**One thing #44 should reuse rather than draft.** JustTCG's terms — effective
2026-07-27, quoted in full under its entry above — grant caching, historical
storage, derived metrics, combination with other sources, and display to end
users, in language written for precisely this use. It is the reference text for
what an unambiguous grant looks like. Ask the shortlisted candidates to confirm
language of that shape rather than composing a question from scratch.

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
