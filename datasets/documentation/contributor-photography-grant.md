# Contributor photography grant

The licence a collector signs before any photograph they took enters this
project's training corpus. It is ADR 0008's approved source class 3, and that
approval is expressly *"subject to the grant naming commercial use, derivative
use and retention expressly"* — so this document is the whole of what makes the
class usable.

> **Read [ADR 0008](../../docs/adr/0008-permitted-training-image-sources.md)
> first if you are editing this template.** Its interpretive rule 1 — *for a
> permission, silence is not a grant* — binds a document this project wrote as
> firmly as it binds anyone else's. A grant that says "you may use my photos for
> your project" grants neither derivative use nor retention, and would be
> refused at ingestion for exactly that reason.

Three parts, and they are separate on purpose: the **grant** is what a person
signs, the **specification** is what they are being asked to photograph, and the
**operator notes** are for whoever runs `tcg-ingest-training-images` afterwards.
Only the first two go to the contributor.

---

## Part A — the grant

Fill the bracketed fields. Nothing else in this part is negotiable: every
sentence in it is a hard requirement of ADR 0008 or a limit this project cannot
exceed.

---

### Photography licence

**Grant reference:** `grant-[YYYY]-[NNN]`
**Contributor:** [full name]
**Contact:** [email]
**Date signed:** [YYYY-MM-DD]

**1. What this covers.** The photographs listed in the schedule below, which you
took, of trading cards you own.

**2. You keep everything.** This licence is **non-exclusive**. You remain the
copyright owner of your photographs and may use, sell, publish or license them
however you like, to anyone, at any time. Signing this gives up none of that.

**3. What you are granting us.** A worldwide, royalty-free, non-exclusive
licence to:

- **use the photographs commercially** — this is a commercial product, and we
  are saying so rather than describing it as research;
- **make derivative works of them**, which expressly includes training,
  evaluating and re-training machine-learning models on them, and producing
  cropped, rotated, colour-corrected and geometrically normalized copies. A
  trained model is a derivative work of the images it was trained on, and this
  clause is what permits it;
- **store and retain them** in a private, versioned corpus, including after this
  licence ends, for the reason in clause 6.

**4. What you are not granting us, and what we are not asking for.** We are
**not** asking for the right to redistribute, publish, share or sell your
photographs, and we will not. They stay in a private corpus. No dataset built
from them is ever published.

**5. What neither of us can grant.** The card in the photograph carries artwork
owned by its publisher. You do not own it, we do not own it, and nothing in this
licence purports to grant any right in it. This licence covers **your
photograph** and nothing depicted in it.

**6. Withdrawing.** Write to us at any time and we will stop using your
photographs. Specifically:

- **What withdrawal reaches:** no further photographs of yours are taken in; the
  ones not yet included in a published dataset version are deleted; none of your
  photographs is included in any dataset version published after we hear from
  you.
- **What withdrawal does not reach:** a dataset version that has already been
  published cannot be changed. A published version is a fixed, immutable record
  of exactly which images a model was trained on — that is what makes a past
  result reproducible — so a photograph already inside one stays inside it, and
  a model already trained is not untrained. This is why clause 3 says retention
  survives the end of the licence.

We would rather you knew that before signing than discovered it afterwards.

**7. No attribution is owed.** You are welcome to say you contributed; nothing
here requires us to credit you, and nothing requires you to be credited.

**8. What you are confirming.** That you took these photographs yourself, that
you own the cards in them, that no one else has a claim on the photographs, and
that they contain no other person and nothing of yours you would not want
retained (see the framing note in Part B).

**9. Compensation.** [state it, or state that there is none — do not leave this
blank]

**10. Governing law.** [jurisdiction]

**Signed:** ............................ **Date:** [YYYY-MM-DD]

#### Schedule

One row per **physical card**, front and back together.

| # | Card | Language | Certification company | Certification number | Date photographed |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |

A certification company and number are optional and only apply if the card has
been professionally graded. They are worth supplying: a grade you can quote from
a slab verifies against the issuing company's own lookup, where a
self-reported one does not. **Supply both or neither** — half a certification is
a record nobody can look up.

---

## Part B — what to photograph

This is a specification, not a preference. A photograph that fails it is not
usable and we would rather not accept it than store something we cannot use.

**Per card:** two photographs, **front and back of the same physical card**,
photographed in one sitting. Two copies of the same card are two entries, not
one — they must be distinguishable, because a training split that put two
photographs of one card on both sides of it would report a score the model has
not earned.

**The card must be:**

- **out of its sleeve, out of its toploader, out of its slab.** A raw card is
  the whole point of this class: it is what the product's users photograph.
- **flat, filling most of the frame, and square to the camera.** Not held at an
  angle, not photographed from the side.
- **the only card in the frame.**

**The photograph must be:**

- **JPEG or PNG**, at most **15 MB** and at most **50 megapixels**;
- at least **1000 pixels on its short edge**, and preferably **2000 or more** —
  below 640 there is nothing measurable at all;
- **in focus**, evenly lit, with **no glare** — no flash, no window reflection,
  no bright spot on the gloss. Glare and blur are the two most common reasons a
  photograph is rejected, and neither is fixable afterwards;
- **not edited.** No sharpening, no denoising, no contrast or exposure
  adjustment, no filters. A sharpened edge is corner whitening that was never
  there, and it would teach the model a defect the card does not have.

These are the conditions the product's own automated quality gate checks, so
the fastest way to tell whether a photograph passes is that it looks like one
you would send a buyer.

**A framing note.** Photograph the card, not your room. A phone photograph
taken on a desk captures the desk, and sometimes hands, mail, and what is on the
wall behind. Crop to the card before sending, or shoot against a plain
background — this is the same restraint the product applies to its own users'
uploads, and it is easier to do once than to undo later.

---

## Part C — operator notes

Not for the contributor.

### Storing the signed grant

**The signed grant does not go in this repository.** It carries a signature, a
name and an email address, and §53's restraint over personal data applies to a
contributor as much as to a user. `tests/test_repository_structure.py` already
refuses images into the index; this is the same rule for the same reason, and it
is not enforced by a test because the file never reaches the tree.

Keep signed grants in the project's private document store, filed under the
grant reference. What reaches the database is the **reference only**.

### The provenance row

Every one of spec §29's nine fields comes off the signed grant, from the
grantor, at the moment of acquisition. None is inferred, and none is left null:

| §29 field | Value | Where it comes from |
| --- | --- | --- |
| `source` | `contributed` | ADR 0008 class 3, fixed |
| `acquisition_method` | `contributed_under_written_grant` | fixed; the pair is the allow-list key |
| `source_reference` | the grant reference, e.g. `grant-2026-001` | Part A header |
| `license` | the grant, named by reference and date signed | Part A header |
| `commercial_use_allowed` | `true` | clause 3 |
| `derivative_use_allowed` | `true` | clause 3 |
| `redistribution_allowed` | **`false`** | clause 4 and clause 5 — never `true`, on any source |
| `permission_notes` | the withdrawal terms, and any limit written into clause 9 | clauses 5, 6, 9 |
| `acquired_at` | the date photographed, from the schedule row | Part A schedule |

`redistribution_allowed` is not a field the contributor can change, and clause 4
is deliberately phrased as something we are not asking for rather than something
they are withholding. The reason is ADR 0008's risk R1: the contributor does not
hold the card's artwork either, so no grant they sign could convey it.

### Ingesting

One invocation is one physical card. The command that records a contributed
card is `tcg-ingest-training-images` — see
[`datasets/README.md`](../README.md) and
[`datasets/schemas/dataset-schema.md`](../schemas/dataset-schema.md) for the
domain it writes into. Pass `--source contributed`,
`--acquisition-method contributed_under_written_grant`,
`--license` naming the grant, `--commercial-use-allowed`,
`--derivative-use-allowed`, `--source-reference` carrying the grant reference,
`--acquired-at` from the schedule row, and `--certification-company` /
`--certification-number` where the schedule supplies them. There is no
`--redistribution-allowed`, by design.

Omitting `--license`, `--commercial-use-allowed` or `--derivative-use-allowed`
is refused by a database constraint, not by the loader — that is ADR 0009's
argument, and it is why a mis-run command stores nothing rather than storing an
image nobody can account for.

### Handling a withdrawal

1. Stop ingesting from that contributor.
2. Delete the training images carrying that grant reference that are **not** yet
   members of a published dataset version. The foreign key from
   `dataset_members` is `RESTRICT`, so this is enforced rather than remembered:
   the delete succeeds for exactly the images clause 6 says it reaches, and
   fails for exactly the ones it says it does not.
3. Record the withdrawal against the grant reference in the private document
   store, with its date.
4. Do not publish a new dataset version including the remaining images.

### What this template must not become

- **Do not add a redistribution clause**, even a narrow one. It is refused by
  R1, not by preference, and a template implying otherwise would be
  representing something the contributor cannot give.
- **Do not soften clause 6.** A grant revocable as to already-versioned images
  would make every dataset version provisional, which is spec §31's immutability
  gone.
- **Do not drop clause 5.** A template silent on the artwork reads as though the
  contributor were granting rights in it.
- **Do not collect more about the contributor than clauses 1 and 8 need.** A
  postal address, a date of birth and a collection inventory are all things this
  grant does not require.
