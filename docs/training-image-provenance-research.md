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

## Review trigger

The ADR that follows this research is reviewed when any of these becomes true —
recorded here because they are properties of the rubric, not of whichever source
wins:

- an approved source's licence or terms change
- a rejected source publishes terms that would change its determination
- a written permission is granted, refused, or withdrawn
- the depicted artwork's rights holder publishes a position on machine-learning use
- the product ceases to be commercial-capable, which would change the test H1 applies
