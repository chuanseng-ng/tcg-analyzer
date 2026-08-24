# ADR 0006 — The V1 market-data provider

- **Status:** accepted
- **Date:** 2026-08-24
- **Refs:** M3, #45, spec §33, §34, §35, §36, §37, §69, §78

## Context

The economic engine cannot be built until it is settled where prices come from,
and the specification refuses to let that be answered by whichever API is
easiest to reach. §34 forbids hard-coding TCGplayer access and mandates this
milestone in terms: *"A dedicated market-provider research milestone must select
a commercially usable provider before production market ingestion is
implemented."* §78 lists the market provider as a **deliberate open decision**,
not an assumption. #52, the M4 provider adapter, is blocked on this record.

**Three of the rights at stake are architectural, not commercial.** §36 requires
a market snapshot per analysis and the invariant that a historical analysis
retains the exact versions it used; §37 forbids calling an external marketplace
during a user request and targets a once-per-day ingestion instead. A provider
whose terms do not permit storing and caching its responses is therefore
**incompatible with the architecture**, not merely inconvenient — the product
would have to call it on the request path, which §37 prohibits. And expected
graded value, `EV = Σ P(g)·V(g)`, transforms provider prices into a new figure
shown to a user, so unclear derived-data rights put the economic engine's entire
output in question.

**The criteria were fixed before any candidate was looked at**, which is the
whole reason M3 is four issues rather than one. #42 wrote the rubric — six
disqualifying hard requirements and ten weighted criteria scored out of 57 —
and with it an evidence standard: a licensing claim is evidenced by the terms
text, quoted, with a URL and a date; a marketing page, an FAQ or a support reply
is not evidence for a licensing claim; **ambiguity is recorded as ambiguity and
never resolved in the project's favour**. #43 then scored thirteen candidates
and shortlisted three. #44 read all three shortlisted candidates' terms end to
end and produced eight determinations each and thirteen numbered risks. All of
that evidence lives in
[`docs/market-provider-research.md`](../market-provider-research.md); this
record cites it rather than restating it.

**What the survey found is that silence, not prohibition, is the dominant
failure mode.** Not one shortlisted candidate forbids what this product needs.
Two of the three simply do not address caching, storage or derived data at all —
and the candidate with the widest coverage has the thinnest terms. The decision
below is therefore a trade between coverage and rights, and the rubric put
licensing in the hard requirements precisely so that trade could not be made
silently.

### The options that were on the table

**PokePriceTracker**, 37/57 — the site trades as PokemonPriceTracker; its terms
name PokePriceTracker throughout, and that is the party they bind. Its §6, "Data
Usage & Commercial Restrictions", is the only clause in the shortlist that
grants storage and caching in terms text, and it goes further than any other
document read: retention *survives cancellation*. Against that, its coverage is
the narrowest of the three — PSA 8/9/10 only, BGS claimed in marketing and
absent from the API reference, TAG nowhere at all — and its terms took effect
five days before they were read.

**PkmnPrices**, 45/57, the highest score in the survey and the widest coverage
in it by a clear margin: TAG named alongside PSA, BGS and twelve other graders,
a full year of history for English *and* Japanese, graded figures taken from
actual eBay sold listings rather than a multiplier bolted onto a raw price, and
a credit budget with four times the headroom a daily refresh needs. Its terms
are eleven short sections that grant nothing about the data. Storage and caching
— hard requirement H2 — are silent, derived data is silent, display is silent,
and the commercial-use permission lives on a pricing page that the terms
incorporate only as to rate limits and credits. Four of eight points unanswered,
including both architectural ones. Selecting it means either overriding the
rubric's own rule about silence or making this record conditional on an email
nobody has sent.

**Scrydex**, 35/57 — one of only two candidates that names TAG, and it adds
population reports. It is silent on the same four points, and its §9 says
*"Except as expressly permitted under these Terms, no rights are granted to you
by implication or otherwise."* That single clause is what separates it from
PkmnPrices: its silence is not a gap to be clarified but the operation of an
express term. Obtaining caching, storage, derived-data and display rights from
Scrydex means obtaining a written authorisation under §4 — a licence
negotiation with an unpublished outcome, not a question a closer reading could
answer. Separately, no published tier supports a daily refresh: Professional's
250,000 monthly credits are 17% of what 49,399 cards need, Enterprise pricing is
unpublished, and §4 and §7 reserve the right to restrict "materially atypical"
usage patterns of exactly that shape.

**PkmnPrices + PokemonPriceTracker as a composition**, 41/57 — live, and it
scores below its own better member once the rubric's flat −3 penalty and its
worst-member reliability rule are applied. More to the point, **a composition
cannot launder rights**: H1–H4 hold per member, so pairing a provider with good
terms against one with good data buys nothing at the licensing layer. It would
carry PokePriceTracker's grants, PkmnPrices' four silences, and every risk of
both, for two subscriptions and two sets of terms to track.

**JustTCG**, 23/57, was not eliminated on rights — its terms are the best in the
survey and the only ones that expressly grant caching, historical storage,
derived metrics and aggregate valuations, combination with other sources, and
display to end users. It scores 0 on historical data (collection begun, access
"coming soon") and 0 on refresh capacity, which removes 15 of 57 points. Its
§§7.1–7.4 remain the reference text for what an unambiguous grant looks like,
and it re-enters if graded coverage leaves beta.

**Manual curation**, 24/57, scored against a stated scope — 500 cards, weekly,
one person, raw plus PSA 9 and PSA 10 — because "manual curation" without a
scope is not a candidate. §69/M3 names it as an acceptable V1 outcome and it
trivially passes every hard requirement, because nobody else's terms apply. Its
cost is coverage: 500 cards against 49,399, which makes
`insufficient_information` the modal answer rather than the exception.

**The rest of the field was eliminated, and for three different kinds of
reason** — a distinction #43 recorded deliberately and this record keeps.
**A refused licence:** PriceCharting (*"Price Data cannot be used in any
software, application, or system that is accessible to third parties… without
express written permission"*), TCGplayer (§34), and scraping as a category.
**A closed door, which is not a refused licence:** Cardmarket (*"Currently, we
are not accepting applications for access to the Cardmarket API"*) and eBay
Marketplace Insights (limited release, closed to new applicants, with the
predecessor Finding API decommissioned 2025-02-04). **An evidence elimination:**
PokeTrace, where neither API documentation nor terms could be located, so no
hard requirement could be assessed at all. TCGdex, this project's own catalog
source, fails upstream for the reason ADR 0004 gave about its card images — MIT
licenses TCGdex's compilation, not Cardmarket's and TCGplayer's prices — and
offers no graded prices in any case.

## Decision

**PokePriceTracker is the V1 market-data provider.** Data is ingested from
`pokemonpricetracker.com` under the terms published at
<https://www.pokemonpricetracker.com/terms>, last updated 2026-08-19.

It enters the system behind §33's `MarketDataProvider` and nothing more. There
is no PokePriceTracker-shaped column on any table, no provider type in
`packages/domain`, and no provider-specific field name, SKU format or quirk
outside the M4 adapter. This record says which implementation V1 ships; it is
not a permanent commitment, and replacing the provider must cost one adapter.

**Rights relied upon.** §78 enumerates six — commercial use, derived data,
storage, caching, display, redistribution — and §34's evaluation list adds
attribution. The union is recorded here, with the competing-product position as
an eighth row because that is the clause which excluded TCGplayer.

| Right | Position |
| --- | --- |
| Commercial use | **Permitted**, and conditional on an active Business or Enterprise subscription: *"Using PokePriceTracker Data for any commercial purpose requires an active Business or Enterprise subscription."* The enumeration that follows names this product's activities directly, including "Building applications, websites, bots, tools, or services that generate revenue" |
| Storage | **Permitted**, and it survives cancellation: *"You may store and cache PokePriceTracker Data in your own systems"*, and separately *"may be retained and used within your own application indefinitely, including after your subscription ends. You are under no obligation to delete it."* |
| Caching | **Permitted, with a condition** — *"Cached data should be refreshed on a reasonable schedule so that end users are not shown materially stale pricing"* |
| Display | **Permitted**, to this application's own end users: *"serve it to the end users of your own application… Serving your own first-party clients… is not considered operating a competing API"* |
| Redistribution | **Prohibited on every tier**, including Business and Enterprise. Not required by V1 — see the product constraint below |
| Attribution | **None owed.** No attribution clause exists in a document read end to end |
| Competing / comparable product | **Prohibited, and narrow enough not to reach this product.** The clause targets "your own competing API that sells or provides the same pricing data to third parties"; a grading advisor is neither a pricing API nor a substitute for one |
| Derived data | **Unclear — narrowed, not resolved.** See below |

**One right is relied upon without a clause granting it, and this record says
so rather than rounding it up.** Derivative works are never named in
PokePriceTracker's terms. What narrows the ambiguity is §6's own enumeration of
permitted commercial uses, which expressly includes "Internal business
analytics, reporting, and decision-making" and "Training artificial intelligence
or machine learning models for commercial products" — both transformations of
the data into something new, and `EV = Σ P(g)·V(g)` is of that family. It is
narrowed and not closed, because that list defines what *requires* a Business
plan rather than what is *licensed*. This is risk R5 in the research document,
and it is unavoidable: **no shortlisted candidate grants derived-data rights
expressly.** What was avoidable is leaving the reliance unstated.

**Commercial use is gated on the plan, so the plan precedes ingestion.** The
Business tier is $99/mo and is the real price of this decision; the free and
$9.99 API tiers do not carry a commercial-use grant. M4 must not ingest real
data before the Business subscription is active, and a credential belongs in
environment configuration, never in code.

**A once-per-day refresh fits the quota with room.** Business provides 200,000
credits per day at 500 requests per minute — the highest published request rate
of any candidate surveyed. Refreshing the whole catalog of 49,399 cards at the
conservative assumption of **one request per card, with no batching**, consumes
49,399 credits, which is **25% of the daily budget**, and takes about **99
minutes**. §37's target is met without needing a batch endpoint to exist.

**Two conditions in the terms constrain the product, and V1 already satisfies
both.** The redistribution prohibition carries a functional test — *"If another
party could use your product in place of a PokePriceTracker subscription to
obtain the data itself, that is redistribution"* — which a per-card grading
recommendation passes comfortably, and which **a bulk price browser, a
downloadable table, a public price-history endpoint or a data export would
not**. None is in V1 scope, and M4 and M7 must keep it that way. Separately, the
caching grant's freshness condition and §36's immutability point in opposite
directions and are both satisfiable: §37's daily ingestion keeps the live path
current, and **the results UI must date-stamp the snapshot it reports** (M4/M5),
because a historical analysis shows an old snapshot by design and a record of a
past date is not a stale price presented as a current one.

**What an M4 `market_providers` row holds.** `name` is **`PokePriceTracker`**,
not PokemonPriceTracker — the terms bind the former. `commercial_use` is `true`,
which this provider can honestly fill and two of the three shortlisted
candidates could not. `license` is "Terms of Service, last updated 2026-08-19"
and `terms_reference` is the URL with that date and the date read. `version` is
**none published** — no shortlisted candidate publishes an API or data version,
which is itself a finding — so §36's `data_version` holds the ingestion date
instead of inventing one.

**TAG is not covered, and the answer is `insufficient_information`.** TAG
appears nowhere in this provider's documentation. Under a rule fixed before any
candidate was seen, that costs 3 points of 57 and is never disqualifying, because
a value that cannot be sourced is reported as uncertain. **A PSA price must
never be substituted for a TAG price, and a TAG value must never be interpolated
from one** — that would be fabricated certainty in the one place a user is
deciding where to spend money. BGS is claimed in marketing and absent from the
API reference, so M4 verifies it against the live API and reports it unavailable
if it is not there, rather than assuming the marketing copy.

**Fallback: manual curation.** §69/M3 names it as an acceptable V1 outcome, it
is the only candidate immune to all thirteen risks in the research document, and
it is what V1 ships if this rights position is withdrawn. #52 implements
whichever of the two is live against the same port, with the same provenance and
licensing metadata.

**Re-entry triggers, so that this decision is reversible without reopening the
rubric.** **PkmnPrices** re-enters as a coverage-motivated candidate — it is the
TAG signal this selection lacks, and it holds a year of English and Japanese
history — if and only if risks R1–R4 are answered in writing; #44 drafted those
four questions and they have not been sent. **Scrydex** re-enters only on a
written authorisation under its §4, because §9 means no reply short of a grant
changes the determination. **JustTCG** re-enters if its graded coverage leaves
beta and its history becomes queryable. In each case the change earns a new ADR;
this one is not rewritten.

**Two standing risks are recorded and are not to be chased.** R12: §6 expressly
declines to disclose collection methods or sourcing arrangements and disclaims
any indemnity against third-party claims, while the documentation names
TCGplayer, Cardmarket and eBay as the sources. R13: every shortlisted
candidate's graded prices derive from eBay sold listings and eBay's own API is
closed, so a change in what eBay exposes propagates to all three at once and no
amount of provider diversity mitigates it. **What a provider's own arrangement
with a marketplace permits is the provider's obligation to hold, not this
project's to license.** Neither is actionable here, and PokePriceTracker's
disclaimer of indemnity means the exposure cannot be contracted away either.

**Review trigger.** This record is reviewed when any of these becomes true:

- the selected provider's terms change
- coverage degrades below a hard requirement
- cost changes materially
- a rate limit stops supporting a once-per-day refresh of the catalog

**The trigger is a scheduled re-read, not a notification.** §14 promises at most
30 days' notice of a material change, qualified by "try" and with materiality
determined at the vendor's sole discretion — the best change-notice provision of
the three shortlisted candidates, which is a low bar. The terms were five days
old when they were read, and a recently rewritten document is likely to move
again. Under the research document's ninety-day rule the next reading is due by
**2026-11-22**, and it happens whether or not any notice arrives.

## Consequences

**What this makes easy.** The one right that blocks the architecture is granted
in terms text rather than inferred from silence, so §36's per-analysis snapshot
and §37's pre-ingested read path are lawful as well as buildable. Retention
surviving cancellation is what makes an immutable historical snapshot lawful
too: a snapshot that had to be deleted when a subscription lapsed would make
every past analysis unreproducible, which is the invariant §36 exists to
protect. The refresh fits the quota at a quarter of the daily budget with no
batch endpoint assumed, so capacity planning has headroom rather than a
dependency on an undocumented feature. Display to this application's own users
is permitted, so the results UI can show a price and not only a conclusion drawn
from one. Nothing owes attribution.

**What this makes expensive.** $99/mo before a single price is ingested, and no
development against real data on a free tier. There is no published SLA and no
established track record, so reliability scored 1 of 3. Graded coverage is PSA
8, 9 and 10 rather than the full scale, and BGS has to be verified before it can
be believed. The terms took effect five days before they were read, which is the
weakest kind of stability evidence, and the review trigger is doing real work
rather than being a formality. And the record carries an unresolved derived-data
reliance openly — that question is the last open point on the strongest
candidate, and it is the one this decision would most like to have closed.

**What this forecloses.** No TAG figure in V1 at all: every TAG value the
pipeline is asked for is `insufficient_information` until a provider that
carries one is added, and it must never be filled in from PSA. No bulk price
browser, downloadable price table, public price-history endpoint or data export,
now or later, while this provider is live — the redistribution test is
functional, so the constraint is on what the product *offers*, not on how it is
implemented. The 45/57 candidate is not what V1 ships, which means the widest
coverage in the survey is on the table only if somebody sends four emails and
gets four answers.

The decision is scoped to market prices. It says nothing about the card catalog,
which is ADR 0004, and nothing about training data, where §29's provenance gate
applies a stricter test than this one — a market price used for anything beyond
the economic engine must be evaluated there, separately, and this record must
not be cited as having settled it.

## Evidence

Every finding relied upon was verified **2026-08-24**, the day this record was
written, so nothing here is stale under the rubric's ninety-day rule. All of it
lives in [`docs/market-provider-research.md`](../market-provider-research.md),
which holds the quoted terms text, the per-criterion scoring with citations, the
refresh arithmetic and the thirteen numbered risks. It is cited rather than
copied so that a finding has one home.

| Candidate | Score | Position |
| --- | --- | --- |
| PkmnPrices | 45/57 | Shortlisted — widest coverage; H2 and H3 rest on silence (R1–R4) |
| PkmnPrices + PokemonPriceTracker | 41/57 | Shortlisted composition — scores below its own better member; cannot launder rights |
| **PokePriceTracker** | **37/57** | **Selected** — the only shortlisted terms granting storage and caching |
| Scrydex | 35/57 | Shortlisted — §9 grants nothing by implication; needs a written §4 authorisation |
| Manual curation | 24/57 | **Retained as the §69/M3 fallback** |
| JustTCG | 23/57 | Not shortlisted — 0 on history and 0 on refresh capacity; rights are the survey's reference text |
| TCGdex pricing | not scored | Eliminated — rights fail upstream, and no graded prices |
| PriceCharting | not scored | Eliminated — a refused licence (H1) |
| TCGplayer API | not scored | Eliminated — spec §34; no access, and a competing-product clause |
| Cardmarket API | not scored | Eliminated — a closed door; applications not being accepted |
| eBay Marketplace Insights | not scored | Eliminated — a closed door; limited release, predecessor decommissioned |
| Scraping a marketplace | not scored | Eliminated — H1 and H4, categorically |
| PokeTrace | not scored | Eliminated on evidence, not rights — no locatable documentation or terms |

Supporting observations:

- The rubric's maximum is 57 across ten weighted criteria, with six hard
  requirements that disqualify regardless of score. A composition is scored as
  one candidate, takes its worst member's reliability, sums rate limits and
  cost, and pays a flat −3.
- Today's catalog is 49,399 cards (33,780 English, 15,619 Japanese) across 381
  sets, which is the figure every refresh calculation in the survey is measured
  against.
- The selected provider's terms name **PokePriceTracker** while the site trades
  as PokemonPriceTracker. The former is the party the terms bind and is what a
  `market_providers` row records.
- Governing law is stated as "the laws of the United States" with no state
  named, which is unusual since general contract law in the United States is
  state law. Recorded as an observation, not a determination.
- The four questions to PkmnPrices, the one to PokePriceTracker and the five to
  Scrydex are drafted in the research document. Sending them is a human action,
  and this record does not wait on the replies — a reply that arrives later
  amends that document in a commit of its own, and changes the decision only by
  way of a new ADR.
