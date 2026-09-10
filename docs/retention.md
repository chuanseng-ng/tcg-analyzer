# Retention and expiry

Spec §54 says an uploaded photograph may contain cards, backgrounds, the user's
hands and their personal surroundings, that original images must not be retained
indefinitely, and that a retention policy must exist before production launch.
This is that policy. It is enforced by
`services/api/src/tcg_api/analysis/retention.py`, which runs hourly inside the
analysis worker.

## The policy

**Everything a user gives this product is deleted seven days after their session
started.** One row survives that, holds nothing a session held, and is named in
the table below rather than left to be discovered.

| What | Kept for | Where it lives |
| --- | --- | --- |
| The original photograph | 7 days | object storage, `images.original_uri` |
| The normalized artifact | 7 days | object storage, `images.normalized_uri` |
| The quality verdict and the transform | 7 days | `images.quality_details`, `images.normalization_details` |
| The preprocessing cache entry | 7 days | it *is* the `images` row |
| The analysis, including spec §57's reproducibility record | 7 days | `analyses` |
| The condition assessment (#187) | 7 days | `analyses.condition_details` — a column on the row above, listed so the omission cannot read as an exemption |
| The per-company grade predictions (#227) | 7 days | `analyses.grade_predictions` — a column on the row above, listed for the same reason |
| The anonymous session | 7 days | `analysis_sessions` |
| The economic configuration, including what the user said they paid | 7 days | `economic_configurations` |
| The §68 feedback row and its hashed return code (#270) | **180 days**, on its own clock | `grade_feedback` — an exemption, justified below |
| A photograph the user consented to keep, and its provenance row (#148) | **Until withdrawn** | `training_images` — the second exemption, justified below. The photograph the *analysis* used is still deleted on day seven; this is a **copy**, under a different key, made at the moment consent was given |

Seven days is `TCG_API_SESSION_TTL_SECONDS`, and it is the only knob. It is
applied once, in Python, when the session is opened —
`analysis_sessions.expires_at` has no column default precisely so that the
period lives somewhere a reviewer reads rather than inside a schema.

There is one period rather than several because there is one cascade:
`analysis_sessions` → `analyses` → `images`, each child owned by its parent
through `ON DELETE CASCADE`. A second horizon would be a second thing to keep in
step, and a policy with an exception nobody tracks is not a policy.

**One row is outside that cascade and is swept anyway.** An analysis
*references* its economic configuration — `analyses.economic_configuration_id`,
spec §57 — rather than owning it, so `ON DELETE CASCADE` runs the wrong way and
the row would survive the session that produced it. It holds spec §45's optional
acquisition cost: what the user says they paid for their card, which is theirs
and is not a fact about a printed card the way a market price is. So the sweep
reads the identifiers before the cascade and deletes the rows after it, which is
the order the foreign key's `RESTRICT` requires. The `economic_configurations`
immutability trigger guards `UPDATE` and not `DELETE` for exactly this reason.

**One row has a horizon of its own.** Spec §68 asks a
user what grade their card actually received, and the answer arrives weeks after
the session that predicted it is gone — so `grade_feedback` (#270) expires on
`TCG_API_FEEDBACK_TTL_SECONDS`, a hundred and eighty days, counted from when the
user asked for a return code rather than from when their session opened. That is
the second horizon the paragraph above argues against, and it is written here
because it is the exception, not because the rule has softened.

What makes it defensible is what the row does **not** hold. There is no
photograph, no object key, no `session_id`, no `analysis_id`, no address and no
acquisition cost — nothing a session ever held and nothing that names a person.
It holds the grade distribution the models predicted, the versions that produced
it, the recommendation the user was shown, the catalog card the analysis
confirmed, and a **hash** of a return code that was displayed once and is stored
nowhere else. The code is a bearer capability rather than an identity: it is not
joined to a session, it is not an account, and holding one proves only that
somebody was shown it. So the seven-day cascade still deletes every photograph
on time, and what survives it is a prediction with no subject.

**This is not the training exemption below, and it must not become one.** A
feedback row is a label with no features: it names no image, joins to no
`physical_copies` row and enters no dataset version. Nothing under
`tcg_api/datasets/` or in `ml/*` may import the domain, and an import-purity
test holds that — §68's own diagram puts validation between a user's answer and
any future training, and that validation is an operator reading the row by hand
(`tcg-review-grade-feedback`), never a pipeline.

**The second exemption is a photograph, and it is the one this document has been
deferring since it was written.** ADR 0008 approves this product's own uploads as
a training-image source *where the user consented*, and issue #148 is the consent.
A user who says yes on the upload screen gets exactly what the paragraph below
demands: retention because **a row says so**.

What makes it defensible is that nothing here is an exception to the sweep at
all. Consenting **copies** the photograph — new bytes under `training/`, a new
`training_images` row carrying spec §29's nine fields, filled at that moment from
the grantor, with `redistribution_allowed` **`false`**. The photograph the
analysis used is a different object under `uploads/`, and it is still deleted on
day seven with everything else its session held; the session row, the analysis
row and the `images` row all go on time and unchanged. No sweep was taught to
skip anything, `SWEPT_NAMESPACES` is still `uploads` and `normalized`, and the
two sweeps below are byte-for-byte what they were.

The row it leaves behind holds no `session_id`, no `analysis_id` foreign key and
no address. It names the analysis as **text**, which is what groups the front and
back of one card, and that identifier resolves to nothing a week later by
construction. There is no account to reach it through and nothing that says who
took the photograph — which is exactly why a **withdrawal code** is minted at the
moment of consent and shown once. It is `tcg_api/codes.py`'s bearer capability
again, stored as a sha256 in `training_images.withdrawal_code_hash` and stored
nowhere else, because the sweep deliberately deletes the session row and there
would otherwise be no way back at all.

**Withdrawal reaches everything not yet inside a published dataset version**, and
says so before anyone consents. `dataset_members.training_image_id` is
`RESTRICT`, so that boundary is enforced rather than remembered: spec §31 makes a
version an immutable record of what a model was trained on, and a version that
could un-include an image would make a past result unreproducible. Annotations,
centering measurements and fingerprints are `CASCADE` and go with the image. When
the last row under a code is gone the code resolves to nothing, and no record
that somebody withdrew is kept — a row kept to remember a decision is the
per-browser identifier §53 argues against, in a second costume.

### Why expiry is the default rather than the exception

§54 asks for analysis data to expire "unless retained for an explicitly
justified purpose". Making retention the default and expiry the exception is
very hard to reverse afterwards, because by the time anybody notices, the data
already exists. So nothing is exempt, and adding an exemption means writing the
justification here first.

**Nothing identifies a person, and nothing is kept that could.** V1 has no
accounts (§53). No IP address and no user agent is recorded anywhere. The
session's `anonymous_session_id` is an opaque token in an HTTP-only cookie —
and it is the reason the sweep *deletes* the session row rather than emptying
it and marking it `purged`: a row kept to record that its images were deleted is
a per-browser identifier kept forever, which is exactly what §53's "do not
permanently tie analyses to personal identity" argues against.
`SessionStatus.EXPIRED` and `SessionStatus.PURGED` therefore exist in the
vocabulary and are written by nothing.

**Retaining an image for training is a different question with a different
answer.** It is a separate, explicitly justified purpose governed by M6's
provenance rules (§29): documented source, licence and commercial-use rights,
per image. It must never happen because a retention sweep skipped something.

That is now a mechanism rather than a promise (#148), and the last sentence is
what shaped it: consent **copies** a photograph into the corpus and changes no
sweep. The justification is above; what the sweeps do is unchanged below.

## What the sweep does

Hourly, for up to two hundred sessions at a time, oldest first:

1. Find the sessions whose `expires_at` has passed, compared against **the
   database's** clock — never the application host's, so a skewed machine cannot
   extend or shorten anyone's retention.
2. For each one, in its own transaction:
   1. read every key its images name — `original_uri` **and** `normalized_uri`;
   2. read the `economic_configuration_id` of every analysis it holds, while
      there are still rows to read them from;
   3. delete those objects from object storage;
   4. delete the session row, which cascades to its analyses and their images;
   5. delete the configurations from step 2, which the cascade did not reach and
      which nothing references any more;
   6. commit.
3. Log `retention.swept` with three counts and nothing else.

### Objects before rows

This ordering is the whole correctness argument, and it is the one thing not to
"simplify".

Deleting a database row is not deleting an image. The row is the *only* pointer
to its objects, so a sweep that deletes rows first and then fails to reach
storage leaves photographs that nothing names and that no row-driven sweep will
ever find again — spec §54's failure, reached through spec §54's own mechanism.
Deleting the objects first means a failure leaves the session still due, and the
next tick tries again; deleting an object that is already gone succeeds, which
is a documented part of the `ObjectStorage` contract and what makes the sweep
safe to re-run.

A storage failure is scoped to one session rather than to the batch, because the
batch is ordered by `expires_at`: aborting the whole batch on the first failure
would re-pick the same rows every hour, and one permanently unreadable key would
stall retention entirely. A skipped session logs
`retention.session_not_swept` and stays due.

### Objects that no row names

The sweep above works from rows, and the row is the only pointer to its
objects. So an object whose row was never committed is invisible to it
permanently — spec §54's failure reached through spec §54's own mechanism.
Three paths leak one: a run killed between writing a normalized artifact and
committing the row that names it (bounded at three per side by the task's retry
limit, since each attempt mints a fresh key), the same leak reached through the
preprocessing cache's copy, and a failed cleanup after a retake, which logs
`image.orphaned`.

A second hourly sweep covers them — `services/api/src/tcg_api/analysis/orphans.py`,
issue #264 — and it works from the key rather than from a row:

1. Ask the database for the time, as the sweep above does, and take the day
   the retention period plus **one day of margin** ago — eight days, today.
2. For each of the **seven day prefixes** ending there, under `uploads/` and
   `normalized/` only, list the keys the object store holds. Up to two hundred
   keys a run.
3. Read which of those keys an `images` row still names — `original_uri` **and**
   `normalized_uri`, the pair the sweep above reads.
4. Delete the rest. Log `retention.orphans_swept` with two counts.

**The margin is a day because a key's date is the day it was minted**, while
retention counts from when the session was *opened*: a session opened at 23:59
names objects under that day and expires almost a full day later. The period
plus a day clears that boundary and the run's own retry window, which is
minutes.

**A key any live row names is never touched**, which is what makes this safe
while the sweep above is behind: a backlogged session's photographs are still
named, so they go when their session does and not before. A row committed
between the listing and the delete names an object minted *today*, which is not
under a prefix this sweep walks.

**It never lists the bucket root.** `generate_key`'s `namespace/YYYY/MM/DD/`
layout exists precisely so this can be a prefix scan (ADR 0002), and
`day_prefix` is the only thing that builds one. The corpus namespaces —
`training/` and `training-normalized/` — are outside the walk on purpose: no
`images` row names one, and retaining a training image is the separately
justified purpose above. That is what a consented photograph is copied into
(#148), so it is reached by neither sweep and goes when its owner withdraws.

The seven-day window is what makes a week of worker downtime recoverable; a day
older than that is never reached again. That is a stated bound rather than an
oversight, and what makes it affordable is that the leak is small by
construction — three objects per side, per killed run.

### Feedback rows past their own expiry

A third hourly sweep, `services/api/src/tcg_api/feedback/store.py` (#270), and
the simplest of the three: one `DELETE` of every `grade_feedback` row whose
`expires_at` has passed, up to two hundred a run, compared against the
database's clock like the two above. It touches no object storage, because the
row names none — which is the whole reason it can be one statement where the
session sweep needs a transaction per session.

Deleting one loses a user's answer as well as the question. That is the intended
trade: the answer is a grade and a certification number, its purpose is §67's
*"predicted grade vs actual submitted grade"*, and a label kept forever is a
label kept for a purpose nobody wrote down. An operator who has validated a row
and wants it to outlive the horizon copies it into `grading_outcomes`, which is
the corpus's record and has provenance rules of its own.

### What is logged

`retention.swept` carries `sessions`, `objects` and `failed` — counts.
`retention.orphans_swept` carries `candidates` and `objects` — counts again.
`retention.feedback_swept` carries `count`, and never a return code: the code
is a bearer capability, so a log line holding one is a log line that can answer
somebody else's question.
`retention.session_not_swept` carries the internal session UUID (never the
cookie's token) and the exception's type name.

Deletion has to be auditable without the audit trail recreating the problem. A
storage key in a log names the photograph that was deleted, and a log nobody
expires is no better than a bucket nobody expires.

## What this does not cover

Three things, named here so that none of them is an exemption nobody wrote
down. A fourth — objects that no row names — was uncovered until #264, and is
now a sweep of its own; it is described above rather than here.

**Market prices and the snapshots of them.** `market_observations`,
`market_providers` and `market_snapshots` are outside the sweep entirely, and
deliberately: nothing in one identifies a person. They are a provider's figures
for a printed card, gathered out of band and never on a user's request. Two of
the three are also load-bearing for reproducibility — spec §36 requires a
historical analysis to resolve the exact prices it used, and
`analyses.market_snapshot_id` is `RESTRICT`, so a snapshot an unexpired analysis
names cannot be deleted at all. What is *not* settled is pruning: a daily refresh
over 49,399 cards is millions of observations a year, the immutability triggers
guard `UPDATE` and not `DELETE` precisely so that a prune stays possible, and
nobody has written the policy that would say which rows go and when. Until
somebody does, nothing is deleted — which is a decision, not an oversight.

**Dead-letter records.** They expire by construction rather than by policy: the
record is a log line carrying the job id, the analysis id, the exception's type
and the attempt count, and never a payload, a traceback or an image URI. There
is nothing in one to retain.

**Backups.** Whatever a deployment's database and bucket backups retain is
outside this document, and a deployment that takes them owes its own answer.

## Running and verifying one

The sweep is scheduled by Celery beat, embedded in the worker process
(`--beat` in `infrastructure/local/docker-compose.yml`). To run one immediately:

```bash
docker compose -f infrastructure/local/docker-compose.yml exec -T worker \
  celery --app tcg_api.analysis.worker call tcg_api.analysis.purge_expired
```

The claim that deletion reaches storage and not only the database is asserted in
three places, because no single one of them can make it:
`services/api/tests/test_retention.py` drives the sweep against a real
PostgreSQL, `packages/shared/tests/test_storage_contract.py` proves `delete`
really removes an object from MinIO, and CI's `compose` job uploads a
photograph, backdates its session, sweeps, and then asks object storage whether
the object is still there. The orphan sweep is split the same way:
`services/api/tests/test_analysis_orphans.py` drives the set it computes against
real PostgreSQL, and the contract suite proves `list` against MinIO.
The feedback sweep needs neither split — it deletes rows and no objects, so
`services/api/tests/test_retention.py` is the whole claim.

The feedback sweep is run the same way, by its own name:

```bash
docker compose -f infrastructure/local/docker-compose.yml exec -T worker \
  celery --app tcg_api.analysis.worker call tcg_api.feedback.sweep_expired
```
