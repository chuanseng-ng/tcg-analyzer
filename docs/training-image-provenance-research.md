# Training-image provenance research

Spec §2.5 requires documented provenance and usage rights for any external image
used in commercial-capable development, and forbids incorporating training data
merely because it is publicly accessible. §28 puts *approved data source* and
*provenance verification* ahead of ingestion in the dataset pipeline, so the
allow-list has to exist before anything is collected. §78 leaves the question
open by name. This document is that research.

It is written in three passes, in this order, and it is the only place the
evidence lives:

| Pass | What it adds |
| --- | --- |
| Rubric | The criteria, the hard requirements, the interpretive rules and the evidence standard — **before any source is looked at** |
| Register | Every candidate source class scored against the rubric |
| Determination | What each plausible source's licence and terms actually permit |

All three land under [#67](https://github.com/chuanseng-ng/tcg-analyzer/issues/67),
which is a single issue where M3's equivalent was four. The ordering survives the
merge: the rubric is committed before the register, and the register before the
determinations.

The decision itself is not here. It is
[ADR 0008](adr/0008-permitted-training-image-sources.md), which cites this
document rather than restating it.

**The rubric is fixed before the register begins, and that ordering is the
point.** Deciding the criteria after seeing the sources is how a large convenient
corpus gets rationalised into a permitted one. Every criterion below is derived
from spec §2.5, §28, §29, §31, §32, §69 and §72 and from nothing else. If one
turns out to be wrong, change it in a commit of its own that says why — do not
quietly reweight it around a source.

**What this document is not.** It is not legal advice and it settles nothing by
argument. It records what documents say, who published them, and when they were
read.

## The evidence standard

The standard is [`market-provider-research.md`](market-provider-research.md)'s,
adopted here rather than restated: a licensing claim is evidenced by the terms or
licence text, quoted, with a URL, the version or effective date, and the date it
was read; documentation and observed behaviour evidence capability claims; every
finding carries the date it was verified and is re-read if it is more than ninety
days older than the ADR; and **ambiguity is recorded as ambiguity, never resolved
in the project's favour and never resolved by inference from anybody's observed
tolerance of somebody else doing it.**

Four additions this domain needs, because images are not prices:

- **The licence is the one the rights holder granted, not the one a platform
  displays.** A dataset page's licence badge is metadata its submitter selected.
  Where the submitter did not own the photographs, no badge they chose grants
  anything, and the badge is recorded as *the submitter's assertion* rather than
  as a licence.
- **A `robots.txt`, an open endpoint and a generous rate limit are not
  permissions.** Technical access is not a grant, and this is the image form of
  the rule above about observed tolerance.
- **Two documents govern every image, not one** — see the second interpretive
  rule below. A determination that names only one of them is incomplete, not
  favourable.
- **An image whose rights cannot be evidenced is recorded as *unknown*, and
  unknown behaves exactly as *false* at §29's gate.** That is spec §29's own
  instruction — *"Training pipeline should reject images where commercial-use
  status is unknown"* — and it is why nothing in this document is left blank.

## Three interpretive rules, stated before the determinations

Fixed here because choosing them after reading the sources is the failure this
issue exists to prevent. The first is carried over from #44 unchanged; the other
two are new and specific to images.

**1 — For a permission, silence is not a grant. For an obligation, silence is
the answer.** Where a licence or terms document does not address commercial use,
derivative use, storage or automated collection, the determination is that the
right is *not granted*. Where a document read end to end imposes no attribution
requirement, it has said that none is owed, and recording that as unclear would
be incoherent.

**2 — The photograph's rights and the depicted card's artwork are separate
questions, and neither answers the other.** ADR 0004 already made this finding in
its catalog form: *"MIT covers TCGdex's compilation; it cannot and does not grant
rights over The Pokémon Company's artwork, and a research ADR is not the place to
assume them."* The generalisation is that a permissively licensed photograph of a
copyrighted card is not thereby clear, and equally that a source is not made
*worse* by an artwork position every other source shares. The artwork question is
therefore recorded on every determination and carried as a standing risk — it is
common to every candidate, first-party photography included, so it cannot
discriminate between them and is deliberately not a hard requirement.

**3 — This project does not rely on fair use, fair dealing, or a text-and-data-
mining exception.** It is commercial-capable, §2.5 requires *documented usage
rights*, and §29 requires a recorded `license` and a `commercial_use_allowed`
value per image. An argument that a use would probably be defensible produces
neither. Where a source's only route is such an argument, the determination is
*not permitted*, and the entry names the argument that was declined, so a later
reader can see it was considered rather than missed.

## Hard requirements

These are disqualifying. A source that fails any one of them is rejected
regardless of how much it holds — **no amount of volume compensates for a licence
that forbids the use.** Two of the four are architectural: §28's pipeline cannot
run without them.

| # | Requirement | Why it is hard, not weighted | Acceptable evidence |
| --- | --- | --- | --- |
| H1 | Commercial use of the image is permitted | This is a commercial-capable product. §2.5 makes documented usage rights mandatory, and §29 makes `commercial_use_allowed` a stored field with no permissive default. | Licence or terms text |
| H2 | Derivative use is permitted | **Architectural.** §28 ends in *Training*, and every crop, augmentation, normalized artifact and trained weight derives from the image. §29 makes `derivative_use_allowed` a field of its own, so the spec already treats it as a separate question from H1. A source permitting commercial use but not derivative use cannot be trained on at all. | Licence or terms text |
| H3 | Storage and retention in a versioned dataset are permitted | **Architectural.** §28 requires ingestion, deduplication and annotation; §31 requires an immutable `dataset_version` a model can be traced to years later. An image that may be viewed but not retained cannot enter a dataset version. | Licence or terms text |
| H4 | No clause forbidding automated collection, machine-learning use, or a competing product | The clause that excludes most marketplaces and, in principle, every grading company — whose graded population is exactly the ground truth a competing predictor would want. It is common, and it is usually the first thing such terms say. | Terms text |

**H1–H4 hold per source, and a combination does not average them.** Merging a
permitted corpus with an unpermitted one produces an unpermitted corpus; #43's
finding that *"a composition cannot launder rights"* holds in its dataset form
and is restated as the third scoring rule below.

**The artwork question is deliberately not among these** — see interpretive rule
2.

## Weighted preferences

Each is scored **0–3** and multiplied by its weight. Maximum **60**.

| # | Criterion | Weight | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | Ground-truth grade label | 3 | no grade at all | a grade for the same card, a different physical copy | the pictured copy's grade, self-reported | the pictured copy's grade, from the issuing company's own record |
| W2 | Domain match to the product's input | 3 | slabbed only | slabbed, high quality | raw, but flat-scanned or studio-lit | raw, hand-held, of the kind §11 actually receives |
| W3 | Front and back coverage | 2 | front only | back occasionally | both, inconsistently paired | both, reliably paired to one physical card |
| W4 | Image quality and consistency | 2 | thumbnails, or unusable under §19's gate | variable, much of it poor | usable, inconsistent framing | consistently sharp, framed and lit |
| W5 | Volume lawfully obtainable | 2 | fewer than 100 images | 100–1,000 | 1,000–10,000 | more than 10,000 |
| W6 | Cost per usable image | 2 | beyond the project's means | high | moderate | negligible |
| W7 | Leakage safety under §32 | 2 | no per-copy identifier; near-duplicates undetectable | source known, copy not | copy inferable from metadata | a stable per-copy identifier travels with the image |
| W8 | Provenance recordable under §29 | 2 | several of the nine fields unknowable | `license` or `acquisition_method` uncertain | all nine recordable, some by inference | all nine known at acquisition, from the grantor |
| W9 | Japanese-card coverage | 1 | none | incidental | present, thinner than English | comparable to English |
| W10 | Time to first usable image | 1 | blocked on a reply nobody has promised | months | weeks | days |

**W1 and W2 carry weight 3 because they are what M8 and M7 respectively cannot be
built without, and they pull against each other on purpose.** The sources richest
in ground-truth grades hold pictures of *slabs*; the product's input is a
hand-held photograph of a *raw* card. A source scoring 3 on both is what this
milestone is looking for, and a source scoring 3 on one and 0 on the other is a
partial answer the register should say so about rather than average away.

**W5 is scored on images the source can lawfully supply, not on images it
holds** — see the second scoring rule.

**W7 is scored, not assumed, because §32 forbids the obvious split.** Random
splitting puts near-identical photographs of one physical card into both train
and test, and the spec's remedy is to group by physical card, source, instance or
certification. A source that cannot identify which images are of the same copy
makes that grouping impossible, and the corpus then reports a test score it has
not earned.

## Three scoring rules decided in advance

All three are decided here, before any source is seen.

### First-party photography is scored, not assumed

It is a candidate like every other and earns its position on the same scale. Its
weaknesses are real — volume, cost per image and time to a labelled example are
where it is weakest, and W5, W6 and W10 exist partly to make that visible. A
rubric that exempted it would be a conclusion wearing a rubric's clothes, and the
register would then be worth nothing as evidence.

The converse holds too. §72 says *"a smaller legally usable dataset is preferable
to a huge dataset with questionable provenance"*, so a low W5 is not
disqualifying and is not treated as one.

### A source is scored on what it can lawfully supply

A collection of 100,000 images of which 2,000 carry an evidenced licence scores
**2,000** on W5, and the other 98,000 are recorded in the entry as what they are.
The alternative — scoring the holding and noting the licence as a caveat — is how
a corpus gets sized against a number nobody may use.

### Synthetic and augmented images are not a source

They inherit the determination of whatever they were derived from, and they are
scored nowhere. This is #43's *"a composition cannot launder rights"* in its
dataset form: an augmentation pipeline applied to an unpermitted photograph
produces an unpermitted photograph, and a generative model trained on one
produces images whose provenance is that training set's. §29's `source` and
`acquisition_method` for a synthetic image name the original, never the
generator.

## Comparison template

One of these per candidate source class, filled in by the register. Copy it
verbatim; **a missing row is a finding, not an omission.** A source rejected at a
hard requirement keeps its full hard-requirement table and its weighted rows read
`n/a` with the failing requirement named — scoring a corpus nobody may use would
be fabrication, and the rows are present to say why they are empty.

```markdown
### <Source class>

- **What it is:** <one line>
- **Rights holder in the photograph:** <who, per the governing document>
- **Status:** approved | rejected (<reason>) | pending written permission
- **Governing document / version / date read:** <url> · <version or effective date> · <YYYY-MM-DD>
- **Readable without an account:** yes / no
- **Volume held vs. lawfully supplied:** <held> / <supplied>

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | pass / fail / unclear | <quote + citation> |
| H2 derivative use | pass / fail / unclear | |
| H3 storage and retention | pass / fail / unclear | |
| H4 no anti-collection, anti-ML or competing-product clause | pass / fail / unclear | |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | | 3 | | |
| W2 domain match | | 3 | | |
| W3 front and back | | 2 | | |
| W4 image quality | | 2 | | |
| W5 volume lawfully obtainable | | 2 | | |
| W6 cost per usable image | | 2 | | |
| W7 leakage safety | | 2 | | |
| W8 provenance recordable | | 2 | | |
| W9 Japanese coverage | | 1 | | |
| W10 time to first usable image | | 1 | | |
| **Total** | | | **/60** | |

- **Artwork-side position:** <what the card's copyright holder publishes, if anything — recorded, never used to rank>
- **Risks and ambiguities:** <recorded, not resolved>
```

## Source register

Surveyed **2026-08-28**. Every finding below was verified on that date unless a
document states its own effective date, which is recorded where it does.

Fifteen candidate source classes. A class is the unit because the determination
usually turns on a property the whole class shares — who owns the photograph —
rather than on one site's drafting.

**How a class that fails a hard requirement is recorded.** It keeps its full
hard-requirement table with the failing clause quoted, and its weighted rows read
`n/a` with the failing requirement named. Scoring a corpus nobody may use would
be fabrication; the rows are present to say why they are empty.

**How a document that could not be read is recorded.** As *unread*, with the
method and the date. Under interpretive rule 1 an unread document grants nothing,
so the determination is *not granted* — which is not the same as a determination
that the terms forbid it, and the entry says which of the two it is. ADR 0004
already set this precedent for terms that could not be reached.

### Scoreboard

| # | Source class | H1 | H2 | H3 | H4 | Score | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | A written data licence from a grading company or bulk submitter | unclear | unclear | unclear | unclear | **48**/60 | **pending written permission** — nobody has been asked |
| 2 | **First-party photography — raw cards, then submitted for grading** | **pass** | **pass** | **pass** | **pass** | **47**/60 | **approved** |
| 3 | **First-party photography — slabs already owned** | **pass** | **pass** | **pass** | **pass** | **42**/60 | **approved**, domain-mismatched |
| 4 | **Commissioned or contributed photography under a written grant** | **pass** | **pass** | **pass** | **pass** | **41**/60 | **approved**, subject to the grant template |
| 5 | **This product's own user uploads, with consent** | **pass\*** | **pass\*** | **pass\*** | **pass** | **40**/60 | **approved in principle, gated** — no consent mechanism exists |
| 6 | CC-licensed photographs on Wikimedia Commons and Flickr | pass | pass | pass | pass | **24**/60 | not approved — eliminated on volume, **not** on rights |
| 7 | Licensed stock and ML data-licensing products | unclear | unclear | unclear | pass | **16**/60 | not approved — eliminated on coverage, **not** on rights |
| 8 | Grading-company cert-lookup and population images | **fail** | **fail** | fail | **fail** | not scored | rejected — H1, H2, H4 on quoted text |
| 9 | Marketplace listing photographs | **fail** | fail | fail | fail | not scored | rejected — the seller owns the photograph and nobody granted it onward |
| 10 | Auction-house archives | fail | fail | fail | fail | not scored | rejected — same class as 9 |
| 11 | Public and community dataset platforms | fail | fail | fail | unclear | not scored | rejected — the platform grants nothing and the uploader's badge is an assertion |
| 12 | Community submissions from forums, chat and video | fail | fail | fail | fail | not scored | rejected — same class as 9 and 11 |
| 13 | Catalog image sources | **fail** | **fail** | fail | pass | not scored | rejected — **already determined by ADR 0004**, and carries no condition signal |
| 14 | Card-scanning app and collection-tracker corpora | unclear | unclear | unclear | unclear | not scored | rejected on evidence — no terms published, nothing to assess |
| 15 | Synthetic and augmented images | n/a | n/a | n/a | n/a | not scored | **not a source** — scoring rule 3 |

\* Class 5's H1–H3 pass on the rights the user would grant, and fail today for
want of anything to grant them with. See its entry: the gap is a consent
mechanism this product does not have, not a licence somebody else holds.

**The register's most important finding is the shape of the top of it.** The
highest-scoring class is the one nobody has agreed to, and the highest-scoring
class that can actually be used scores one point below it. That is not a
coincidence and it is the issue's own prediction: the most useful data is the
most legally constrained, because a grading company's intake images are labelled
with the exact ground truth a competing predictor needs.

### 1 — A written data licence from a grading company, break company or bulk submitter

- **What it is:** a negotiated licence over images taken *before* submission, paired with the grade the submission received
- **Rights holder in the photograph:** the grader or the submitter, depending on who took it
- **Status:** pending written permission — **nobody has been asked**
- **Governing document / version / date read:** none exists; this class is defined by the absence of published terms
- **Readable without an account:** n/a
- **Volume held vs. lawfully supplied:** millions / **zero, today**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | unclear | No document. A grant would create one; nothing grants it now |
| H2 derivative use | unclear | As above |
| H3 storage and retention | unclear | As above |
| H4 no anti-collection, anti-ML or competing-product clause | unclear | The competing-product question is the live one: this product predicts the grade the counterparty sells |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 3 | 3 | 9 | The grade is the counterparty's own record of what it issued |
| W2 domain match | 3 | 3 | 9 | A grader's intake images are of **raw** cards. Whether any grader retains them, and would licence them, is the open question |
| W3 front and back | 2 | 2 | 4 | Intake photography is per-card; pairing is likely but undocumented |
| W4 image quality | 3 | 2 | 6 | Controlled capture |
| W5 volume lawfully obtainable | 3 | 2 | 6 | Conditional on the grant; scored on what a grant would supply, with the status column carrying that it does not exist |
| W6 cost per usable image | 0 | 2 | 0 | Unpublished, and priced for enterprises where it is priced at all |
| W7 leakage safety | 3 | 2 | 6 | A certification number is a per-copy identifier — §32 names it |
| W8 provenance recordable | 3 | 2 | 6 | All nine fields would come from the grantor |
| W9 Japanese coverage | 2 | 1 | 2 | A Japanese-market grader or submitter is a separate counterparty |
| W10 time to first usable image | 0 | 1 | 0 | Blocked on a reply nobody has promised |
| **Total** | | | **48/60** | |

- **Artwork-side position:** unchanged — see [R1](#risk-register). A grader licensing its intake photography licenses its own photographs, not The Pokémon Company's artwork.
- **Risks and ambiguities:** [R6](#risk-register). **This is the highest-scoring class in the register and it is not approved**, because H1–H4 are unclear and interpretive rule 1 does not resolve them in this project's favour. It is the M3 shape exactly: the candidate with the best data has the worst documentation of rights.

### 2 — First-party photography, raw cards then submitted for grading

- **What it is:** photograph a card we own, submit it for grading, and label the photograph with the grade that comes back
- **Rights holder in the photograph:** this project
- **Status:** **approved**
- **Governing document / version / date read:** none — no third party's terms govern a photograph of an object we own, taken by us
- **Readable without an account:** n/a
- **Volume held vs. lawfully supplied:** the cards on hand / all of them

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass** | We are the author and first owner of the photograph. No document restricts our use of it |
| H2 derivative use | **pass** | As above |
| H3 storage and retention | **pass** | As above |
| H4 no anti-collection, anti-ML or competing-product clause | **pass** | No terms exist to contain one. Submitting a card for grading is buying a service, and a grading company's site terms bind use of its **site** — they are not a licence over photographs taken before the card was posted |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 3 | 3 | 9 | The grade is issued for the pictured copy, by the company, and is verifiable against its own cert lookup |
| W2 domain match | 3 | 3 | 9 | Raw, hand-held, taken the way §11 receives them — the only class in the register scoring 3 here with a label attached |
| W3 front and back | 3 | 2 | 6 | Both sides, paired to one physical card, because we take them |
| W4 image quality | 3 | 2 | 6 | Under our control, and §19's gate is the standard to hit |
| W5 volume lawfully obtainable | 0 | 2 | 0 | Fewer than 100 in any near-term plan. §72 says this is the right trade, and it is still a 0 |
| W6 cost per usable image | 1 | 2 | 2 | A grading submission per labelled example, plus shipping and insurance. High, not prohibitive at small scale |
| W7 leakage safety | 3 | 2 | 6 | A certification number, and our own instance identifier besides |
| W8 provenance recordable | 3 | 2 | 6 | All nine of §29's fields known at acquisition, from ourselves |
| W9 Japanese coverage | 2 | 1 | 2 | A property of the collection rather than of the source; recorded as thinner pending the collection's composition |
| W10 time to first usable image | 1 | 1 | 1 | The photograph takes minutes; the **label** takes a grading turnaround, which is months |
| **Total** | | | **47/60** | |

- **Artwork-side position:** unchanged — [R1](#risk-register) applies here as it applies to every class. Owning the card is not owning the artwork.
- **Risks and ambiguities:** [R1](#risk-register), [R2](#risk-register), [R7](#risk-register), [R8](#risk-register). **W1 and W2 both score 3 and no other class manages it**, which is what makes this the approved primary source despite scoring 0 on volume.

### 3 — First-party photography, slabs already owned

- **What it is:** photograph graded cards we bought, through the case
- **Rights holder in the photograph:** this project
- **Status:** **approved**, and domain-mismatched
- **Governing document / version / date read:** none, as class 2
- **Readable without an account:** n/a
- **Volume held vs. lawfully supplied:** the slabs on hand / all of them

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass** | Our photograph of our object |
| H2 derivative use | **pass** | As above |
| H3 storage and retention | **pass** | As above |
| H4 no anti-collection, anti-ML or competing-product clause | **pass** | No terms exist. The **label** inside the slab is the grader's, and whether a photograph of it may be reproduced is a question this entry does not need to answer, because nothing in V1 reads it |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 3 | 3 | 9 | Printed on the label by the company that issued it |
| W2 domain match | **0** | 3 | **0** | Slabbed only. The product's input is a raw card, and V1 excludes slab analysis outright |
| W3 front and back | 3 | 2 | 6 | Both, paired |
| W4 image quality | 3 | 2 | 6 | Under our control, allowing for the case's glare |
| W5 volume lawfully obtainable | 1 | 2 | 2 | Buying slabs is cheaper per label than submitting, and still small |
| W6 cost per usable image | 1 | 2 | 2 | A slab costs more than the raw card, and the premium is the label |
| W7 leakage safety | 3 | 2 | 6 | The certification number is printed on the image itself |
| W8 provenance recordable | 3 | 2 | 6 | All nine fields |
| W9 Japanese coverage | 2 | 1 | 2 | As class 2 |
| W10 time to first usable image | 3 | 1 | 3 | Days — the label already exists |
| **Total** | | | **42/60** | |

- **Artwork-side position:** unchanged — [R1](#risk-register).
- **Risks and ambiguities:** [R1](#risk-register), [R2](#risk-register). **This is the register's worked example of the W1/W2 tension the rubric predicted**: 3 on the grade label, 0 on domain match, and a total that flatters it. Approving it makes it lawful to hold; it does not make it a condition-training source, and M7 owns that judgement.

### 4 — Commissioned or contributed photography under a written grant

- **What it is:** a collector photographs their own cards to our specification and signs a licence
- **Rights holder in the photograph:** the contributor, until the grant
- **Status:** **approved**, subject to the grant naming all of H1–H3 expressly
- **Governing document / version / date read:** the grant, which this project authors
- **Readable without an account:** n/a
- **Volume held vs. lawfully supplied:** whatever contributors hold / whatever the grant covers

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass** | By construction — the grant says so, or there is no grant |
| H2 derivative use | **pass** | As above. It is named expressly, because interpretive rule 1 applies to a document we wrote as firmly as to anyone else's |
| H3 storage and retention | **pass** | As above, including survival after a contributor withdraws — a dataset version is immutable under §31 and cannot un-include an image |
| H4 no anti-collection, anti-ML or competing-product clause | **pass** | We do not write one |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 2 | 3 | 6 | The pictured copy's grade, self-reported. 3 where the contributor supplies a certification number that verifies |
| W2 domain match | 3 | 3 | 9 | Raw, hand-held, to our specification |
| W3 front and back | 2 | 2 | 4 | Specified, not controlled |
| W4 image quality | 2 | 2 | 4 | As above |
| W5 volume lawfully obtainable | 1 | 2 | 2 | Bounded by how many people sign |
| W6 cost per usable image | 1 | 2 | 2 | Compensation, plus the administrative cost of a grant per contributor |
| W7 leakage safety | 2 | 2 | 4 | The contributor is a group key; the copy is one where a certification number travels |
| W8 provenance recordable | 3 | 2 | 6 | All nine fields, from the grantor, at acquisition |
| W9 Japanese coverage | 2 | 1 | 2 | A property of who signs |
| W10 time to first usable image | 2 | 1 | 2 | Weeks — a template, then people |
| **Total** | | | **41/60** | |

- **Artwork-side position:** unchanged — [R1](#risk-register). A contributor cannot grant more than they have, and they do not have the artwork either.
- **Risks and ambiguities:** [R1](#risk-register), [R2](#risk-register). The grant template is a deliverable this issue does not write; it is named in the ADR's consequences as M6's.

### 5 — This product's own user uploads, with consent

- **What it is:** the photographs users already upload to `POST /analyses/{id}/images`, retained for training where the user has agreed
- **Rights holder in the photograph:** the user
- **Status:** **approved in principle, gated** — the mechanism that would obtain the grant does not exist
- **Governing document / version / date read:** none yet; the consent text would be this project's
- **Readable without an account:** n/a
- **Volume held vs. lawfully supplied:** every upload / **zero, today**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass**, conditional | Conditional on consent this product does not yet ask for. [`retention.md`](retention.md) anticipates exactly this: *"Retaining an image for training is a different question with a different answer… a separate, explicitly justified purpose governed by M6's provenance rules (§29)"* |
| H2 derivative use | **pass**, conditional | As above — and the consent must name it, per interpretive rule 1 |
| H3 storage and retention | **pass**, conditional | As above, and it needs an exception to §54's sweep, which today deletes every uploaded image when its session expires |
| H4 no anti-collection, anti-ML or competing-product clause | **pass** | No third party's terms are involved |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | **0** | 3 | **0** | No grade. The user is asking what grade the card *might* get — that is the whole product |
| W2 domain match | 3 | 3 | 9 | Not merely close to the inference distribution: it **is** the inference distribution |
| W3 front and back | 3 | 2 | 6 | §11 requires both sides before an analysis can run |
| W4 image quality | 2 | 2 | 4 | Variable, but §19's gate already scores every one of them |
| W5 volume lawfully obtainable | **0** | 2 | **0** | Zero until a consent mechanism exists. Unbounded after — scored on what it can lawfully supply today, per scoring rule 2 |
| W6 cost per usable image | 3 | 2 | 6 | Negligible; the image has already been captured and paid for |
| W7 leakage safety | 3 | 2 | 6 | `images.sha256` and the analysis identifier are already per-copy, and #39 already deduplicates on the hash |
| W8 provenance recordable | 3 | 2 | 6 | All nine fields, from the grantor, at the moment of upload |
| W9 Japanese coverage | 2 | 1 | 2 | Whatever users upload |
| W10 time to first usable image | 1 | 1 | 1 | Months — consent design, a §53-compatible mechanism with no accounts, and a carve-out in a retention sweep that currently forbids it |
| **Total** | | | **40/60** | |

- **Artwork-side position:** unchanged — [R1](#risk-register).
- **Risks and ambiguities:** [R1](#risk-register), [R2](#risk-register), [R5](#risk-register). **The two zeros are not the same kind of zero.** W1 is structural and permanent: this class can never carry a grade, because the user does not have one. W5 is a mechanism this project can build. That asymmetry is why the class is approved in principle rather than rejected, and why it can never be the only source.

### 6 — CC-licensed photographs on Wikimedia Commons and Flickr

- **What it is:** photographs of cards released by their photographers under CC BY, CC BY-SA or CC0
- **Rights holder in the photograph:** the photographer, who has granted a licence
- **Status:** **not approved** — eliminated on volume, **not** on rights
- **Governing document / version / date read:** <https://commons.wikimedia.org/wiki/Commons:Licensing> · read 2026-08-28
- **Readable without an account:** yes
- **Volume held vs. lawfully supplied:** near zero / near zero, and the reason is the point

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **pass** | Commons requires it: *"Commercial use of the work must be allowed."* CC BY and CC0 both grant it |
| H2 derivative use | **pass** | Commons requires it: *"Publication of derivative work must be allowed."* |
| H3 storage and retention | **pass** | Commons requires the licence be *"perpetual (non-expiring) and non-revocable"*, which is what §31's immutable dataset version needs |
| H4 no anti-collection, anti-ML or competing-product clause | **pass** | CC licences contain none |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 0 | 3 | 0 | None |
| W2 domain match | 2 | 3 | 6 | Typically flat-scanned or studio-lit |
| W3 front and back | 0 | 2 | 0 | Fronts, overwhelmingly |
| W4 image quality | 2 | 2 | 4 | Variable |
| W5 volume lawfully obtainable | **0** | 2 | **0** | Near zero, and Commons' own policy is why — see below |
| W6 cost per usable image | 3 | 2 | 6 | Free |
| W7 leakage safety | 0 | 2 | 0 | No per-copy identifier |
| W8 provenance recordable | 2 | 2 | 4 | Recordable, some by inference from the file page |
| W9 Japanese coverage | 1 | 1 | 1 | Incidental |
| W10 time to first usable image | 3 | 1 | 3 | Days |
| **Total** | | | **24/60** | |

- **Artwork-side position:** **this class is where the artwork layer is corroborated by somebody with no stake in this project's answer.** Commons *"does not accept"* photographs of copyrighted items, holding that *"a photograph of a copyrighted item is considered a derivative work in US jurisdiction"*, and names *"photographs of copyrighted action figures, toys, etc."* as rejected. A photograph of a Pokémon card is that category. Commons exists to maximise free reuse and still refuses the class, which is a stronger corroboration of interpretive rule 2 than anything this project could write for itself.
- **Risks and ambiguities:** [R1](#risk-register). The volume is near zero **because** the policy above holds; any such file present on Commons is there in error, and building on files a host would delete is not a supply.

### 7 — Licensed stock and ML data-licensing products

- **What it is:** a stock library's ordinary licence, or the separate data-licensing products some libraries sell for model training
- **Rights holder in the photograph:** the library's contributor, licensed through the library
- **Status:** **not approved** — eliminated on coverage, **not** on rights
- **Governing document / version / date read:** <https://submit.shutterstock.com/help/en/articles/10594694-shutterstock-data-licensing-and-the-contributor-fund> · read 2026-08-28
- **Readable without an account:** yes for the data-licensing description; the licence itself is negotiated
- **Volume held vs. lawfully supplied:** hundreds of millions of images / **effectively none of this subject**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | unclear | A standard stock licence does not cover model training; a data licence would. Neither has been obtained, so nothing is granted |
| H2 derivative use | unclear | As above. That the libraries sell a **separate** product for training is itself evidence that the ordinary licence does not cover it |
| H3 storage and retention | unclear | As above |
| H4 no anti-collection, anti-ML or competing-product clause | pass | None known |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | 0 | 3 | 0 | None |
| W2 domain match | 1 | 3 | 3 | Studio product photography, where any exists |
| W3 front and back | 0 | 2 | 0 | None |
| W4 image quality | 3 | 2 | 6 | High, and beside the point |
| W5 volume lawfully obtainable | **0** | 2 | **0** | No stock library carries condition-graded Pokémon singles at any volume |
| W6 cost per usable image | 0 | 2 | 0 | Enterprise data licensing, unpublished |
| W7 leakage safety | 0 | 2 | 0 | No per-copy identifier |
| W8 provenance recordable | 3 | 2 | 6 | A stock library's whole business is recording it |
| W9 Japanese coverage | 0 | 1 | 0 | None |
| W10 time to first usable image | 1 | 1 | 1 | A negotiation |
| **Total** | | | **16/60** | |

- **Artwork-side position:** unchanged — [R1](#risk-register), and a library licensing a contributor's photograph does not license the card in it.
- **Risks and ambiguities:** none open. **This is the register's JustTCG**: rights that could be made adequate, over a subject the source does not carry. It re-enters only if a library acquires a graded-card corpus, which nothing suggests.

### 8 — Grading-company cert-lookup and population images

- **What it is:** the images PSA, TAG and Beckett publish beside a certification number or a population report
- **Rights holder in the photograph:** the grading company
- **Status:** **rejected** — H1, H2 and H4, on quoted text
- **Governing document / version / date read:** <https://app.collectors.com/collectorsuseragreement> · last updated 2026-07-09 · read 2026-08-28 · governs PSA. <https://taggrading.com/pages/terms> · last revised 2026-07-08 · read 2026-08-28. Beckett: <https://www.beckett.com/terms> · **unread** — HTTP 403 to automated fetch on 2026-08-28
- **Readable without an account:** PSA's user agreement yes; PSA's Public API documentation **no**, sign-in required; TAG yes; Beckett **no** by the method used
- **Volume held vs. lawfully supplied:** tens of millions / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | Collectors §4: *"It is strictly prohibited to modify, transmit, distribute, reuse, repost, 'frame' or use the Content for public or commercial purposes… without written permission."* TAG §5.2(f): a user may not *"exploit the Services for any commercial purpose"* |
| H2 derivative use | **fail** | Collectors §4: *"you may not copy, modify, delete, publish, transmit, participate in the transfer or sale, lease or rental of, create derivative works from or in any way exploit any of the Content."* **This is an express refusal, not a silence** — the only H2 in the register that is. TAG §5.1(a) grants a licence that is *"personal, non-assignable, non-sublicensable, non-transferrable, and non-exclusive"* |
| H3 storage and retention | fail | Follows from H2 on both documents; neither addresses retention separately, and under interpretive rule 1 neither grants it |
| H4 no anti-collection, anti-ML or competing-product clause | **fail** | Collectors §11(9) bars use of any *"'deep-link', 'page-scrape', 'robot', 'spider', or other automatic device, program, algorithm, or methodology… to: (1) retrieve, scrape, access, acquire, copy, or monitor any portion"*. TAG §5.2(j) bars any *"robot, spider, crawlers, scraper, or other automatic device… that intercepts, 'mines,' scrapes, extracts, or otherwise accesses the Services"* |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1 ground-truth grade label | n/a | 3 | n/a | Eliminated at H1, H2 and H4 |
| W2 domain match | n/a | 3 | n/a | |
| W3 front and back | n/a | 2 | n/a | |
| W4 image quality | n/a | 2 | n/a | |
| W5 volume lawfully obtainable | n/a | 2 | n/a | |
| W6 cost per usable image | n/a | 2 | n/a | |
| W7 leakage safety | n/a | 2 | n/a | |
| W8 provenance recordable | n/a | 2 | n/a | |
| W9 Japanese coverage | n/a | 1 | n/a | |
| W10 time to first usable image | n/a | 1 | n/a | |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register), and irrelevant here, because the photographer layer already refuses.
- **Risks and ambiguities:** [R3](#risk-register) Beckett unread, [R10](#risk-register) PSA's API terms behind a sign-in. **Neither changes the class determination**: two of the three companies refuse in quoted text, and an unread document grants nothing.
- **Neither readable document mentions machine learning, AI training, or text and data mining at all.** Both were read for it. The silence changes nothing — §4 and §5.2(f) do not need to name training to forbid the commercial reuse it is part of — and it is recorded because the absence is what a later reader would otherwise go looking for.
- **This is the confrontation the issue asked to have early.** These images carry the exact ground truth this project needs, which is precisely why the terms that govern them are the most restrictive in the register.

### 9 — Marketplace listing photographs

- **What it is:** the photographs sellers upload to eBay, TCGplayer, Cardmarket, Mercari and Yahoo! Auctions Japan
- **Rights holder in the photograph:** **the seller — a private individual — not the marketplace**
- **Status:** **rejected**
- **Governing document / version / date read:** <https://static.jp.mercari.com/en/global_tos> · effective 2026-02-05 · read 2026-08-28. eBay <https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259> · **unread**, request timed out twice on 2026-08-28. TCGplayer <https://help.tcgplayer.com/hc/en-us/articles/205004918-Terms-of-Service> · **unread**, HTTP 403 on 2026-08-28. Cardmarket · **unread**, HTTP 403, and ADR 0006 already recorded its API applications closed
- **Readable without an account:** Mercari yes; the other three not by the methods used
- **Volume held vs. lawfully supplied:** the largest corpus of card photographs in existence / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | **The determination does not need the marketplaces' terms, which is why three of them being unread does not change it.** The seller owns the photograph. A marketplace holds at most a licence to display it, and a licence it cannot sublicense is one it cannot pass on. Mercari's terms, read end to end, contain **no** clause licensing seller photographs onward, and under interpretive rule 1 that silence is not a grant |
| H2 derivative use | fail | Not granted by the seller, who has not been asked, and not grantable by the marketplace |
| H3 storage and retention | fail | As above |
| H4 no anti-collection, anti-ML or competing-product clause | fail | Not determinable for three of five, and moot: there is nothing to collect lawfully. Mercari §4.6 restricts reverse-engineering and derivative works **of the Platform** and is deliberately **not** cited as an anti-scraping clause, because it is not one |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Eliminated at H1 as a category |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register), and doubly refused: a marketplace listing photograph fails on the photographer layer *and* the artwork layer.
- **Risks and ambiguities:** [R4](#risk-register), [R9](#risk-register). **A correction to the desk pass that produced this entry's candidate list**, recorded rather than quietly fixed: it reported Mercari as carrying an express anti-scraping clause. Reading the document found §4.6 to be about the Platform, not about listings, and no photograph-licensing clause at all. The class determination is unchanged and now rests on the seller's ownership rather than on a clause that is not there.

### 10 — Auction-house archives

- **What it is:** Goldin, Heritage, Fanatics Collect and similar — professional, high-resolution photography with the slab grade in the lot title
- **Rights holder in the photograph:** the auction house
- **Status:** **rejected** — the same determination as class 9, from the other side
- **Governing document / version / date read:** none read; the class is disposed of by the rule below rather than by a document
- **Readable without an account:** yes, generally
- **Volume held vs. lawfully supplied:** hundreds of thousands / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | fail | The house owns its photography and licenses it to nobody. Under interpretive rule 1 an absent grant is not a grant, and **no document was found offering one** |
| H2 derivative use | fail | As above |
| H3 storage and retention | fail | As above |
| H4 no anti-collection, anti-ML or competing-product clause | fail | Not determined; moot at H1 |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Eliminated at H1 |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register).
- **Risks and ambiguities:** none open. **Worth recording that this is the best image quality in the field and that it changes nothing**, because the rubric is not a quality contest — and worth recording that it is slabbed, so it would score 0 on W2 even if it were licensed.

### 11 — Public and community dataset platforms

- **What it is:** card-grading and card-image datasets hosted on Kaggle, Hugging Face, Roboflow Universe and GitHub
- **Rights holder in the photograph:** whoever took it — **almost never the uploader**
- **Status:** **rejected**
- **Governing document / version / date read:** <https://huggingface.co/terms-of-service> · effective 2022-09-15 · read 2026-08-28. Kaggle and Roboflow dataset pages · **unread**, body not retrievable on 2026-08-28
- **Readable without an account:** Hugging Face yes; the per-dataset licence is per dataset
- **Volume held vs. lawfully supplied:** thousands of images across a handful of card-grading datasets / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | **The platform grants nothing and says so.** Hugging Face: *"When Content contains notice of a reasonable and customary license… such Content is intended to remain under the terms of such license"* — the licence is the uploader's, not the platform's. And the uploader warrants rights they mostly do not hold: *"You represent and warrant that you have ownership, control, and responsibility for the Content you post."* A warranty is not a grant, and a false warranty is not a grant either |
| H2 derivative use | fail | As above. A licence badge over images the uploader did not own conveys nothing, because there was nothing to convey |
| H3 storage and retention | fail | As above |
| H4 no anti-collection, anti-ML or competing-product clause | unclear | Per platform; moot at H1 |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Eliminated at H1 as a category |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register), and this is the class where it has actually been enforced; see the determination below.
- **Risks and ambiguities:** [R1](#risk-register). **The class-level rule, stated once so it need not be re-derived per dataset:** a licence badge selected by a submitter who did not own the photographs grants nothing, which is the evidence standard's first addition doing the work it was written for. A dataset re-enters this register only if its images are traced to a rights holder who granted them — at which point it is being assessed as that source, not as this one.

### 12 — Community submissions from forums, chat and video

- **What it is:** photographs posted to grading subreddits, Discord servers and video frames
- **Rights holder in the photograph:** the poster
- **Status:** **rejected**
- **Governing document / version / date read:** none read; disposed of by the same rule as classes 9 and 11
- **Readable without an account:** varies
- **Volume held vs. lawfully supplied:** very large / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | fail | The poster owns the photograph and has licensed the platform, not us. Where a platform does license content for AI training it sells that licence, and it has not been bought |
| H2 derivative use | fail | As above |
| H3 storage and retention | fail | As above |
| H4 no anti-collection, anti-ML or competing-product clause | fail | Platform terms in this class routinely contain one |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Eliminated at H1 as a category |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register).
- **Risks and ambiguities:** none open. **The argument declined here, named per interpretive rule 3**: that a photograph posted publicly to a community existing to discuss grading is impliedly offered for grading-related use. It is not a licence, nobody wrote it down, and §2.5 requires documented rights.

### 13 — Catalog image sources

- **What it is:** TCGdex's card assets and `pokemontcg.io`'s images
- **Rights holder in the photograph:** the scan's producer for the scan; The Pokémon Company for the artwork
- **Status:** **rejected** — already determined by [ADR 0004](adr/0004-the-canonical-card-catalog-source.md), and cited rather than re-derived
- **Governing document / version / date read:** ADR 0004, accepted 2026-08-18
- **Readable without an account:** yes
- **Volume held vs. lawfully supplied:** one image per catalog card / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | **fail** | ADR 0004: *"MIT covers TCGdex's compilation; it cannot and does not grant rights over The Pokémon Company's artwork, and a research ADR is not the place to assume them."* `pokemontcg.io` publishes no licence at all, which ADR 0004 recorded as `license: null` |
| H2 derivative use | **fail** | As above |
| H3 storage and retention | fail | As above — ADR 0004 declined even to mirror these images into object storage for **display**, which is a weaker use than training |
| H4 no anti-collection, anti-ML or competing-product clause | pass | MIT contains none |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Eliminated at H1 and H2 |
| **Total** | | | **n/a** | |

- **Artwork-side position:** this class **is** the artwork question — see [R1](#risk-register). ADR 0004 reached it first, in its catalog form.
- **Risks and ambiguities:** none open. **Recorded even though the rights refuse first**: these are scans of pristine cards. They carry no condition signal and no grade, so they would train no condition model and no grade predictor even if they were licensed. A source can be both unlicensed and useless, and saying so stops a later reader re-opening it as a near miss.

### 14 — Card-scanning app and collection-tracker corpora

- **What it is:** the image corpora accumulated by collection-scanning applications
- **Rights holder in the photograph:** the app's users, licensed to the app on unpublished terms
- **Status:** **rejected on evidence, not on rights**
- **Governing document / version / date read:** none locatable
- **Readable without an account:** no
- **Volume held vs. lawfully supplied:** unknown / **zero**

| Hard requirement | Verdict | Evidence |
| --- | --- | --- |
| H1 commercial use | unclear | No published data-licensing terms could be located, so no hard requirement can be assessed |
| H2 derivative use | unclear | As above |
| H3 storage and retention | unclear | As above |
| H4 no anti-collection, anti-ML or competing-product clause | unclear | As above |

| Criterion | Score | Weight | Points | Evidence |
| --- | --- | --- | --- | --- |
| W1–W10 | n/a | | n/a | Not assessable |
| **Total** | | | **n/a** | |

- **Artwork-side position:** unchanged — [R1](#risk-register).
- **Risks and ambiguities:** none open. **This is #43's PokeTrace elimination in its dataset form**: rejected for want of anything to read, not for what a document says. It re-enters if such a corpus is offered on published terms, and it would then be assessed as class 1.

### 15 — Synthetic and augmented images

**Not a source.** Scoring rule 3 governs: an augmented, rendered or generated
image inherits the determination of whatever it was derived from, and it is
scored nowhere in this register. §29's `source` and `acquisition_method` for such
an image name the original, never the generator, and its
`derivative_use_allowed` is the original's.

Recorded as an entry rather than omitted, because the omission would read as an
oversight and because the tempting move — augment an unapproved corpus until the
outputs no longer resemble the inputs — is precisely the laundering #43 ruled out
for market data and rule 3 rules out here.

## The artwork determination

The register above determines the photographer's layer per class. Interpretive
rule 2 requires a second determination, and it is made once, because it is
identical for every class in the register.

- **Rights holders:** The Pokémon Company, Nintendo, Creatures Inc. and GAME FREAK — the four ADR 0004 named
- **Governing documents / dates read:** <https://www.pokemon.com/us/legal/terms-of-use> · last updated 2023-06-15 · read 2026-08-28. <https://www.pokemon.com/us/legal/information> · read 2026-08-28
- **Readable without an account:** yes

| Point | Determination | Evidence |
| --- | --- | --- |
| Ownership | **The publisher's** | *"all content on the Service, including articles, artwork, screen shots, graphics, logos, downloads and other files, is the property of Pokémon"* |
| Commercial use | **Not granted** | The grant is *"to use the content of Service made available to you for personal, noncommercial home use only"* |
| Derivative use | **Not granted** | No clause grants it. Interpretive rule 1 applies |
| Storage in a corpus | **Expressly prohibited** | Among the prohibited uses: *"Download quantities of content to a database for any reason."* This is the closest thing to an anti-corpus clause in any document read for this issue, and it is aimed at exactly the act §28 calls *ingestion* |
| Automated collection | Prohibited | Prohibits use of *"any unauthorized third-party software (e.g. bots, mods, hacks, and scripts) to modify or automate operation within the Service"* |
| Machine-learning use | **Not addressed** | The document was read for it. It contains no mention of machine learning, AI, model training, or text and data mining |
| A route to a licence | **None published** | The Fan Content clause runs one way — creators grant the publisher a licence — and no licensing-enquiry route is published |
| Enforcement | **Actual, against a training dataset** | On 2024-02-09 Tracer, acting for The Pokémon Company International, sent Hugging Face a takedown naming the image dataset `lambdalabs/pokemon-blip-captions` as containing *"unauthorized [copies and/or uses] of copyrighted material, such as but not limited to the Pokémon characters"*. Recorded at <https://huggingface.co/datasets/huggingface-legal/takedown-notices/blob/main/2024/2024-02-09-Pokemon-Company-International.md> · read 2026-08-28 |

**In plain language.** *May:* nothing beyond personal, non-commercial home use of
the publisher's own site content. *May not:* download quantities of content to a
database, or use it commercially. *Not known:* whether training a model on
photographs of physical cards one owns falls within any of this — no document
addresses it, and none of the four rights holders publishes a position.

**Why this is a standing risk and not a hard requirement.** It attaches to the
card, so it attaches identically to every class in the register: a first-party
photograph of a card we own carries the same artwork position as a scraped one. A
test every candidate fails equally cannot rank them, and promoting it to a hard
requirement would reject the entire register including the classes whose
photographer layer is unimpeachable — which would not be a cautious answer, it
would be an incoherent one. It is therefore carried as [R1](#risk-register) and
it constrains **what may be done with an approved corpus** rather than which
sources may enter it. The operative constraint is [R2](#risk-register):
`redistribution_allowed` is `false` for every approved class, and no dataset is
ever published.

**Two things this determination deliberately does not do.** It does not decide
whether any use is lawful — that is not a question a repository document can
answer, and interpretive rule 3 already refuses to build on an argument that it
would be. And it does not treat the 2024 takedown as settling anything: it was a
notice, not a judgment, and what it evidences is that the rights holder acts on
image training datasets, which is a fact about risk rather than about rights.
ADR 0004's own hedge applies here unchanged.

## Risk register

Every ambiguity above, numbered so the ADR cites rather than restates.
**"Blocks §28"** means the risk reaches the dataset pipeline itself, rather than
a preference or an exposure.

| # | Class | Unresolved | Blocks §28 | What would resolve it |
| --- | --- | --- | --- | --- |
| R1 | **All** | The artwork layer. Personal non-commercial use only; bulk download to a database expressly prohibited; machine-learning use unaddressed; no licensing route published; and one enforcement against an image training dataset on record | no — it constrains every class equally and so cannot rank them | Nothing available. **A standing risk, not an open question** |
| R2 | All approved | `redistribution_allowed` is `false` even where this project owns the photograph, because the artwork in it is not ours to redistribute | no | Nothing available. Standing, and it is the operative consequence of R1 |
| R3 | 8 | Beckett's terms returned HTTP 403 to automated fetch on 2026-08-28, so BGS has no determination of its own | no — PSA and TAG both refuse in quoted text and the class does not turn on the third | A browser reading, recorded in a commit of its own |
| R4 | 9 | eBay's user agreement timed out twice and TCGplayer's returned 403 on 2026-08-28 | no — the class fails on the seller's ownership, which no marketplace's terms can cure | A browser reading. TCGplayer is separately closed by ADR 0006 and spec §34 |
| R5 | 5 | No consent mechanism exists, V1 has no accounts (§53), and §54's sweep deletes every uploaded image when its session expires | **yes, for that class** — it supplies zero images until this is built | A consent mechanism and a documented retention exception, both M6's work |
| R6 | 1 | The highest-scoring class in the register and nobody has been asked. Whether any grader or bulk submitter retains pre-encapsulation images, and would licence them to a product that predicts the grade they sell | no — the milestone has an approved source without it | A written licence. The request is drafted below |
| R7 | 2, 3, 4 | Volume. Every approved class scores 0 or 1 on W5, and §2 requires a calibrated model — a distribution claiming 80% must be right about 80% of the time, which is a claim about sample size | no — it reaches M8's acceptance, not this milestone's | More images, or M8 declining to claim calibration it cannot evidence |
| R8 | 2 | The label arrives a grading turnaround after the photograph, so the approved primary class's throughput is set by a third party's queue | no | Nothing available. A schedule fact, recorded so M7 and M8 plan against it |
| R9 | 9 | Mercari's terms are silent on seller photographs rather than adverse | no | Nothing useful: Mercari does not own the photographs, so a reply from Mercari would grant nothing. Standing |
| R10 | 8 | PSA's Public API documentation and terms are behind a sign-in and were not read | no | Creating an account to read them, which is a decision rather than a research step — ADR 0004 set that precedent |

**R1, R2 and R9 are standing risks and are not to be chased.** R1 and R2 have no
available resolution, and R9's counterparty is not the rights holder. Recording
them is the whole of what can be done, which is why the ADR states them rather
than carrying them as work.

**R5 and R6 are the two worth spending time on**, and they are the two that would
change what the corpus can contain: R5 unlocks the only class matching the
inference distribution at volume, and R6 the only class pairing raw images with
issued grades at volume. Neither blocks this milestone.

## §29's nine fields, filled in

What an M6 ingestion would write per approved class, so a row is traceable to a
reading recorded here. This is the confirmation #67 owes that §29's fields are
sufficient to record what was decided.

| §29 field | 2 — first-party raw | 3 — first-party slabs | 4 — contributed | 5 — user uploads |
| --- | --- | --- | --- | --- |
| `source` | `first_party` | `first_party` | `contributed` | `product_upload` |
| `source_url/reference` | the submission's certification number | the slab's certification number | the signed grant's identifier | the `analysis_id` |
| `acquisition_method` | `photographed_before_submission` | `photographed_owned_slab` | `contributed_under_written_grant` | `uploaded_by_user_with_consent` |
| `license` | owned outright | owned outright | the grant, by identifier and date | the consent text, by version |
| `commercial_use_allowed` | `true` | `true` | `true` | `true` **only where consent was recorded** |
| `derivative_use_allowed` | `true` | `true` | `true` | `true`, as above |
| `redistribution_allowed` | **`false`** | **`false`** | **`false`** | **`false`** |
| `permission_notes` | R1 — the artwork layer, by reference | R1 | R1, plus the grant's own limits | R1, plus the consent version |
| `acquired_at` | the capture timestamp | the capture timestamp | the capture timestamp | the upload timestamp |

**The fields are sufficient, and one of them earns its place immediately.**
`redistribution_allowed` is `false` on **every** approved class, including the
three where this project took the photograph itself, and it is false for a reason
no other field records: R1. A schema carrying only `commercial_use_allowed` would
have lost that distinction, and a corpus that is ours to train on but not ours to
publish would have looked identical to one that is ours to do anything with.

**Two rules carried forward from #44, restated because these apply per image
rather than per provider.** Do not default an unclear determination to `true` —
§29 says the pipeline rejects an image whose commercial-use status is unknown, so
*unknown* and *false* must reach the gate as the same answer, and a boolean that
says `true` because nobody wanted a null is the failure this milestone exists to
prevent. And every one of the nine fields is written **at acquisition, from the
grantor** on all four approved classes, which is what makes the gate checkable
rather than aspirational.

**One field the approved classes make load-bearing that §29 does not carry.** §32
requires the split to group by physical card, source, instance or certification,
and none of §29's nine fields identifies the physical **copy**. For classes 2 and
3 the certification number in `source_url/reference` serves; for classes 4 and 5
nothing does. That is a gap in the *dataset* schema rather than in the provenance
schema, it is M6's to close, and it is recorded here so M6 does not discover it
after ingesting.

## Requests to put to rights holders

Sending these is a human action and **this document does not wait on the
replies** — the determinations above stand on the documents as they are today,
the gaps are recorded as risks, and the ADR decides on that evidence. A reply
arriving later amends this section in a commit of its own, and changes the
decision only through a new ADR.

Only one class is worth a request. Classes 8 through 14 refuse in terms, or
refuse by the ownership of the photograph, and a reply changes neither.

**Grading companies, break companies and bulk submitters — class 1.** One
request, four questions, each answerable yes or no. Ask for a licence, not a
clarification: unlike M3's silent providers there is no ambiguity here to
resolve, there is simply no grant.

1. Are images captured of a card **before** it is encapsulated retained after grading, and for how long? (R6 — if the answer is no, the class is empty and the rest is moot)
2. May such images, paired with the grade the submission received, be licensed for use as machine-learning training data? (R6)
3. Would that licence cover storage in a private, versioned corpus retained after the licence ends, so a past model stays reproducible? (R6 — this is §31's requirement and the one most likely to be refused)
4. Does the counterparty regard a product that **predicts** a grade as a competing product? (R6, and better asked than assumed)

The fourth is the one the issue's own implementation note predicted, and it
should be asked first rather than last if only one question can be put: an answer
of yes ends the class regardless of the other three.

**Nothing is asked of The Pokémon Company, and that is a decision rather than an
omission.** No licensing route is published, the Fan Content clause runs one
direction only, and an enquiry that produces a refusal converts a standing risk
into a documented refusal — which is a worse position than the one recorded at
R1, not a better one. It is recorded here so it is visible, and it is reversible.

## Re-verification

Every finding in this document was verified **2026-08-28**. Under the evidence
standard's ninety-day rule, anything the ADR relies on is re-read if the ADR
lands after **2026-11-26**. It does not: the ADR is dated the same day.

Three things are worth revisiting sooner than the rule requires, for reasons in
their own drafting or absence:

- **Beckett, eBay, TCGplayer and Cardmarket** were never read at all (R3, R4). They are not stale, they are absent, and the ninety-day rule does not reach them.
- **The Collectors user agreement** was last updated 2026-07-09 and **TAG's terms** 2026-07-08 — seven and eight weeks before they were read, and within a day of each other. Two documents in the same class revised that close together is worth noticing rather than assuming coincidental.
- **The Pokémon.com terms of use** were last updated 2023-06-15: over three years old, and unchanged across the period in which machine-learning training became the question everybody else's terms started answering. That silence is currently determinative under interpretive rule 1, and it is the single finding here most likely to change.

## Review trigger

The ADR that follows this research is reviewed when any of these becomes true —
recorded here because they are properties of the rubric, not of whichever source
wins:

- an approved source's licence or terms change
- a rejected source publishes terms that would change its determination
- a written permission is granted, refused, or withdrawn
- the depicted artwork's rights holder publishes a position on machine-learning use
- the product ceases to be commercial-capable, which would change the test H1 applies
