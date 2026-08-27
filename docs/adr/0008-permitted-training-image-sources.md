# ADR 0008 — Permitted training-image sources

- **Status:** accepted
- **Date:** 2026-08-28
- **Refs:** M6, #67, spec §2.5, §28, §29, §31, §32, §69, §72, §78

## Context

Spec §2.5 makes provenance mandatory: *"Any external image, price, annotation, or
dataset used for commercial-capable development must have documented provenance
and usage rights"*, and *"Training data must not be incorporated merely because it
is publicly accessible."* §28 puts *approved data source* and *provenance
verification* ahead of ingestion, so an allow-list has to exist before anything is
collected rather than after. §78 leaves which sources qualify open by name, and
§72 states the tie-breaker in advance: *"A smaller legally usable dataset is
preferable to a huge dataset with questionable provenance."*

Nothing can start without this. M7 and M8 are the obvious dependents, but the
closer one is that **epic #7 has not been decomposed** — it says it *"is
decomposed once the training-data permissions decision below is resolved, [as]
writing detailed task issues now would encode assumptions the specification
forbids."* #67 blocks its own milestone's planning.

**Two accepted decisions pointed here and deliberately did not answer.** ADR 0004
closes with *"nothing about training data, where the §29 provenance gate applies
a stricter test than this one: catalog images used for anything beyond display
must be evaluated there, separately, and this ADR must not be cited as having
settled it."* ADR 0006 carries the same sentence for market prices. This is the
record they were reserving.

**ADR 0004 also supplied the finding this decision generalises.** It refused to
mirror catalog images because *"MIT covers TCGdex's compilation; it cannot and
does not grant rights over The Pokémon Company's artwork."* Two layers, not one.
Training images add a third: the **photographer's** copyright in the photograph,
which ADR 0004 never had to reach because a catalog scan and its compilation
travel together.

**The criteria were fixed before any source was looked at**, and #67 preserved
that ordering inside a single issue by committing the rubric before the register.
The hard requirements, the ten weighted criteria, the three interpretive rules
and the evidence standard were all derived from the specification and from
nothing else. All of that evidence lives in
[`docs/training-image-provenance-research.md`](../training-image-provenance-research.md);
this record cites it rather than restating it.

**What the register found is that the useful data and the available data barely
overlap.** The issue predicted it — *"the most useful data is the most legally
constrained"* — and the register puts numbers on it: the two highest-scoring
classes are separated by one point out of sixty, and one of them requires a
licence nobody has been asked for.

### The options that were on the table

**A written data licence from a grading company, break company or bulk
submitter, 48/60 — the highest score in the register, and not approved.** A
grader's intake photography is of *raw* cards and is paired with the grade that
submission received, which is the only combination in the field that scores 3 on
both weight-3 criteria at volume. Nobody has been asked, so all four hard
requirements are unclear, and interpretive rule 1 does not resolve them in this
project's favour. It is M3's Scrydex shape exactly: the best data behind the
worst documentation of rights.

**First-party photography of raw cards, then submitted for grading, 47/60.**
Photograph a card we own, submit it, label the photograph with the grade that
comes back. Every hard requirement passes because no third party's terms govern
it. It scores 0 on volume and 1 on cost and on time-to-first-image, and those are
real: a labelled example costs a grading submission and a grading turnaround.

**First-party photography of slabs already owned, 42/60.** Lawful on identical
reasoning, and the register's worked example of the trade the rubric was built to
expose: 3 on the grade label, **0 on domain match**, and a total that flatters it.
V1's input is a raw card and V1 excludes slab analysis outright.

**Contributed photography under a written grant, 41/60.** The one class whose
governing document this project drafts. Bounded by how many people sign.

**This product's own user uploads, with consent, 40/60.** The only class that
does not merely approximate the inference distribution but *is* it, at whatever
volume the product sees. It carries **no grade**, permanently, because the user
is asking what grade the card might get — that is the product. And it supplies
nothing today: there is no consent mechanism, V1 has no accounts, and §54's sweep
deletes every uploaded image when its session expires.
[`docs/retention.md`](../retention.md) anticipated exactly this and deferred it
here: *"Retaining an image for training is a different question with a different
answer."*

**The rest of the field was rejected, for four different kinds of reason.**

*Refused in terms.* Grading-company cert-lookup and population images. The
Collectors user agreement — which governs PSA — bars *"creat[ing] derivative
works from or in any way exploit[ing] any of the Content"* and separately bars
robots and page-scraping; TAG's terms grant a licence that is *"personal,
non-assignable, non-sublicensable"* and bar commercial exploitation and scraping
in their own clauses. This is the confrontation the issue asked to have early,
and it resolves against the project on quoted text rather than on silence.

*Refused by whose photograph it is.* Marketplace listings, auction-house
archives, community posts. The seller, the house or the poster owns the
photograph; the platform holds at most a display licence it cannot sublicense.
**This is why four unreadable platform agreements changed nothing** — no clause
in them could have granted what the platform does not hold.

*Refused one hop up the chain.* Public dataset platforms. Hugging Face states
that hosted content *"is intended to remain under the terms of such license"* —
the uploader's licence, not the platform's — and requires uploaders to warrant
rights they largely do not have. A licence badge selected by somebody who did not
own the photographs conveys nothing, because there was nothing to convey.

*Not refused at all, and still not approved.* Catalog images were already settled
by ADR 0004 and additionally carry no condition signal. CC-licensed photographs
on Wikimedia Commons pass **every hard requirement** and score 24/60 on volume
alone. Licensed stock passes on rights it could buy and scores 16/60 because no
library carries the subject. Card-scanning corpora publish nothing to read.
Synthetic images are not a source at all: they inherit their original's
determination, which is #43's *"a composition cannot launder rights"* in its
dataset form.

## Decision

**Four sources are approved, all of them first-party or first-party by grant, and
every third-party corpus is rejected.**

| # | Approved source | Standing |
| --- | --- | --- |
| 1 | **Photographs we take of raw cards we own, submitted for grading** | The **primary** source. The only class scoring 3 on both a ground-truth grade label and domain match |
| 2 | Photographs we take of graded slabs we own | Lawful to hold. **Not thereby a condition-training source** — M7 decides that, and the domain mismatch is on the record |
| 3 | Photographs contributed under a written grant naming commercial use, derivative use and retention expressly | Subject to the grant template, which M6 writes |
| 4 | This product's own user uploads, where the user consented | **Approved in principle and supplying nothing** until a consent mechanism and a retention exception exist |

**Rights relied upon**, identical across all four because all four are granted by
us or to us directly:

| Right | Position |
| --- | --- |
| Commercial use | **Granted.** We are the author and first owner (1, 2), or the grant says so (3, 4). §29's `commercial_use_allowed` is `true` |
| Derivative use | **Granted**, on the same footing. It is named expressly in the class-3 grant and the class-4 consent, because interpretive rule 1 binds a document we wrote as firmly as anyone else's |
| Storage and retention | **Granted**, including retention after a contributor withdraws — §31 makes a dataset version immutable, so it cannot un-include an image |
| **Redistribution** | **Not granted, on any of the four.** `redistribution_allowed` is `false` even where we took the photograph, because the artwork in it is not ours to redistribute |
| Attribution | **None owed.** No governing document imposes one; under interpretive rule 1 a document read end to end that requires no attribution has said none is owed |
| Competing product | **Unconstrained.** No terms exist to contain such a clause. A grading company's site terms bind use of its site; they are not a licence over photographs taken before a card was posted to it |
| The depicted artwork | **Not granted, and not grantable by any of these counterparties.** Carried as risk R1 |

**The gate enforces the allow-list, and *unknown* is *false*.** §29 requires the
training pipeline to reject an image whose commercial-use status is unknown, so
the ingestion gate admits an image only where `source` names one of the four
above and all three of `commercial_use_allowed`, `derivative_use_allowed` and the
recorded `license` are present. A null, an empty string and an absent field are
one answer, and it is refusal. **Do not default an unclear determination to
`true`** — that is #44's rule, restated because it now applies per image rather
than per provider, and a boolean that reads `true` because nobody wanted a null
is the failure this milestone exists to prevent.

**§29's nine fields are sufficient, and `redistribution_allowed` is why they had
to be nine.** The research document fills all nine in for each approved class. A
schema carrying only `commercial_use_allowed` would have lost the one distinction
that actually binds here: a corpus that is ours to train on but not ours to
publish would have been indistinguishable from one that is ours to do anything
with.

**No dataset produced under this decision is ever published**, and that follows
from the row above rather than from caution. `datasets/manifests` holds
identifiers and hashes, `datasets/documentation` holds prose, and
`tests/test_repository_structure.py` already refuses to let an image into git's
index at all. The manifests are publishable; the images are not.

**The artwork layer is stated, not solved.** The publisher's terms grant *"personal,
noncommercial home use only"*, expressly prohibit *"Download quantities of content
to a database for any reason"*, are silent on machine-learning use, publish no
licensing route, and its agent has served a takedown on an image training dataset
(2024-02-09). It attaches to the card, so it attaches identically to every source
in the register, approved and rejected alike. **A test every candidate fails
equally cannot rank them**, which is why it is risk R1 and not a hard
requirement, and why it constrains what may be done with an approved corpus
rather than which sources may enter it. This ADR does not decide whether any use
is lawful, and interpretive rule 3 already refuses to rest anything on an argument
that it would be. **It is an engineering record, not legal advice.**

**The label is what costs money, and that is the trade this decision makes.** The
approved primary source produces one labelled example per grading submission,
after a grading turnaround. §72 chose this trade in advance — a smaller legally
usable dataset over a larger questionable one — and it is worth naming what it
costs: risk R7 records that §2 requires calibration, that a distribution claiming
80% must be right about 80% of the time, and that this is a claim about sample
size. **M8 may find the approved corpus too small to claim calibration. The
answer then is to say so, not to widen the allow-list** — `insufficient_information`
is a legitimate output and a rejected source does not become permitted because a
model wanted it.

**One request goes out, and nothing waits on the reply.** The register's
highest-scoring class needs a written licence from a grading company, break
company or bulk submitter; the four questions are drafted in the research
document, and the fourth — whether the counterparty regards a grade *predictor*
as a competing product — is asked first, because an answer of yes ends the class
regardless of the other three. A reply amends the research document in a commit
of its own and changes this decision only through a new ADR.

**Nothing is asked of The Pokémon Company, deliberately.** No licensing route is
published, its Fan Content clause runs one direction only, and an enquiry that
produced a refusal would convert a standing risk into a documented refusal —
which is a worse position than R1, not a better one. Recorded so the omission is
visible, and reversible.

**Two documents were never read, and that is a finding rather than a gap.**
Beckett, eBay, TCGplayer and Cardmarket all refused automated fetch on
2026-08-28 (R3, R4). An unread document grants nothing, so none of them changes a
determination: their classes fail on quoted text from their peers, or on who owns
the photograph. ADR 0004 set this precedent for terms that could not be reached.

**Re-entry triggers, so this decision is reversible without reopening the
rubric.** A rejected source re-enters if it publishes terms granting H1–H3, if
its images are traced to a rights holder who grants them, or if a written licence
is obtained. It re-enters **through the rubric as written**, scored on the same
scale — not by exception.

**Review trigger.** Reviewed when an approved source's licence or terms change;
when a rejected source publishes terms that would change its determination; when
a written permission is granted, refused or withdrawn; when the artwork's rights
holder publishes a position on machine-learning use; or if the product ceases to
be commercial-capable, which would change the test H1 applies. **The trigger is a
scheduled re-read, not a notification.** Under the research document's ninety-day
rule the next reading is due by **2026-11-26**, and it happens whether or not
anything prompts it.

## Consequences

**What this makes easy.** Epic #7 can be decomposed: there is an approved source,
so a dataset schema, a provenance schema, an ingestion gate and an annotation UI
all have something concrete to be built against. The gate itself is simple
because the allow-list is short and every approved image's provenance is known at
acquisition, from the grantor — there is no class where a field has to be
inferred, researched later, or left null. And the two rejected categories that
would otherwise be revisited endlessly, marketplaces and dataset platforms, are
each disposed of by a single rule that does not need re-deriving per site.

**What this makes expensive.** Ground truth now costs a grading submission and a
grading turnaround per labelled example, and the project's throughput is set by a
third party's queue (R8). Class 4 — the only source at volume in the right
distribution — costs a consent mechanism, a §53-compatible one with no accounts,
and a documented exception to the retention sweep (R5). Class 3 costs a grant
template and the administration of one signature per contributor. None of these
is work M6 was scoped for, and all three now belong to it.

**What this forecloses.** Every large corpus. There is no path in this decision to
tens of thousands of images in the near term, and M7 and M8 must be planned
against hundreds. It forecloses publishing any dataset, so this project's corpus
cannot become a public artifact or a benchmark others cite, and it cannot be
shared with a collaborator without a separate decision. It forecloses the
convenient move of augmenting a borrowed corpus until the outputs no longer
resemble the inputs. And it leaves R1 standing: the artwork layer is unresolved,
unresolvable from the documents that exist, and carried rather than closed.

The decision is scoped to **training images**. It says nothing about the card
catalog, which is ADR 0004, and nothing about market prices, which is ADR 0006.
It does not decide which approved images train which model, or whether a
domain-mismatched approved source should be trained on at all — those are M7's
and M8's, and approving a source makes it lawful to hold rather than sensible to
use. And it does not settle the artwork question: R1 is recorded as standing, and
**this record must not be cited as having resolved it.**

## Evidence

Every finding relied upon was verified **2026-08-28**, so none of it is stale
under the research document's ninety-day rule; the next reading is due by
2026-11-26. Licensing positions were determined from the terms and licence text
itself, quoted, with the URL and the document's own effective date where it
states one. Four documents refused automated fetch and are recorded as unread
rather than assessed.

| # | Source class | Score | Position |
| --- | --- | --- | --- |
| 1 | A written data licence from a grading company or bulk submitter | 48/60 | Pending written permission — nobody has been asked (R6) |
| 2 | **First-party photography — raw cards, then submitted** | **47/60** | **Approved — the primary source** |
| 3 | **First-party photography — slabs already owned** | **42/60** | **Approved**, domain-mismatched |
| 4 | **Contributed photography under a written grant** | **41/60** | **Approved**, subject to the grant template |
| 5 | **This product's own user uploads, with consent** | **40/60** | **Approved in principle, gated** (R5) |
| 6 | CC-licensed photographs on Wikimedia Commons and Flickr | 24/60 | Not approved — volume, **not** rights |
| 7 | Licensed stock and ML data-licensing products | 16/60 | Not approved — coverage, **not** rights |
| 8 | Grading-company cert-lookup and population images | not scored | Rejected — H1, H2 and H4 on quoted text |
| 9 | Marketplace listing photographs | not scored | Rejected — the seller owns the photograph |
| 10 | Auction-house archives | not scored | Rejected — as class 9 |
| 11 | Public and community dataset platforms | not scored | Rejected — the platform grants nothing |
| 12 | Community submissions from forums, chat and video | not scored | Rejected — as classes 9 and 11 |
| 13 | Catalog image sources | not scored | Rejected — already determined by ADR 0004 |
| 14 | Card-scanning app and collection-tracker corpora | not scored | Rejected on evidence — nothing published to read |
| 15 | Synthetic and augmented images | not scored | **Not a source** — inherits its original's determination |

Supporting observations:

- The rubric's maximum is 60 across ten weighted criteria; two of them carry weight 3, and they are the ground-truth grade label and the match to the product's actual input.
- Four hard requirements, of which two are architectural: derivative use, because §28 ends in training, and retention, because §31 requires an immutable dataset version.
- The two highest-scoring classes are one point apart, and the higher of them is unavailable. That is the register's central finding, not an artifact of the weights.
- **Wikimedia Commons corroborates the artwork layer from outside this project.** A project whose purpose is free reuse *"does not accept"* photographs of copyrighted items, holding that *"a photograph of a copyrighted item is considered a derivative work in US jurisdiction"* and naming action figures and toys. It has no stake in this decision's outcome.
- A gap §29 does not cover, found while filling it in: **none of its nine fields identifies the physical copy**, which §32 needs to group a leakage-safe split. A certification number serves for classes 1 and 2 and nothing serves for classes 3 and 4. That belongs to the dataset schema, and it is M6's to close.
- Ten risks are recorded, R1–R10. Three are standing and are not to be chased; two — the consent mechanism and the written licence — are the only ones whose resolution would change what the corpus can contain.
