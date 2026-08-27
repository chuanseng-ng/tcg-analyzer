# Retention and expiry

Spec §54 says an uploaded photograph may contain cards, backgrounds, the user's
hands and their personal surroundings, that original images must not be retained
indefinitely, and that a retention policy must exist before production launch.
This is that policy. It is enforced by
`services/api/src/tcg_api/analysis/retention.py`, which runs hourly inside the
analysis worker.

## The policy

**Everything a user gives this product is deleted seven days after their session
started, and nothing is kept beyond that.**

| What | Kept for | Where it lives |
| --- | --- | --- |
| The original photograph | 7 days | object storage, `images.original_uri` |
| The normalized artifact | 7 days | object storage, `images.normalized_uri` |
| The quality verdict and the transform | 7 days | `images.quality_details`, `images.normalization_details` |
| The preprocessing cache entry | 7 days | it *is* the `images` row |
| The analysis, including spec §57's reproducibility record | 7 days | `analyses` |
| The anonymous session | 7 days | `analysis_sessions` |
| The economic configuration, including what the user said they paid | 7 days | `economic_configurations` |

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

### What is logged

`retention.swept` carries `sessions`, `objects` and `failed` — counts.
`retention.session_not_swept` carries the internal session UUID (never the
cookie's token) and the exception's type name.

Deletion has to be auditable without the audit trail recreating the problem. A
storage key in a log names the photograph that was deleted, and a log nobody
expires is no better than a bucket nobody expires.

## What this does not cover

Four things, named here so that none of them is an exemption nobody wrote down.

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

**Objects that no row names.** The sweep works from rows. A worker killed
between writing a normalized artifact and committing the row that names it
leaves an object behind — bounded at three per side by the task's retry limit,
since each attempt mints a fresh key — as does a failed cleanup after a retake,
which logs `image.orphaned`. Both carry `ponytail:` comments where they occur.
Closing this needs a `list` method on the `ObjectStorage` port and a sweep by
prefix and age; `generate_key`'s `namespace/YYYY/MM/DD/` layout exists so that
that sweep can be a prefix scan rather than a full listing. Not built, because
nothing yet needs it.

**Dead-letter records.** They expire by construction rather than by policy: the
record is a log line carrying the job id, the exception's type and the attempt
count, and never a payload, a traceback or an image URI. There is nothing in one
to retain.

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
the object is still there.
