# Budgeting spec §67's eight signals against the first measured run

- Date: 2026-09-07
- Refs: M10, #267, #266, #271, #272, spec §67, §53, §54
- Measured: commit `7d35415`, CI run
  [34063674946](https://github.com/chuanseng-ng/tcg-analyzer/actions/runs/34063674946)
  (2026-09-06 22:19–22:23 UTC), the `compose` and `e2e` jobs
- Runner: GitHub-hosted `ubuntu-latest` — image `ubuntu-24.04`, runner
  `2.337.0`, 4 CPUs, 15.61 GiB

Spec §67 names eight things to track. #266 put each one on a log line — a
`duration_ms` on every pipeline step, a name on every event, and the table in
[`development.md`](development.md#logs) that says which line carries which
signal. This document is the other half of epic #11's first checklist item: a
**budget** for each signal, and the **first measurement** of each, read off one
run of the CI jobs that drive the whole stack. Like
[`image-quality-gate-research.md`](image-quality-gate-research.md) it is a
dated record, not a living table: a later measurement is a new dated section
beneath this one, never an edit of these figures.

A budget here is a number a reviewer compares a run against — by hand, off the
`Logs` step of a `compose` or `e2e` job, or off `docker compose logs` on a
developer's stack. **Nothing asserts a budget in CI, on purpose**: a duration
asserted on a shared runner is flaky by construction, and a flaky assertion is
one that gets loosened until it means nothing. There is no dashboard and no
alerting either; §67 is logs plus this document until something exists to host
one. What *does* read these numbers is code — #272's task time limits and
#271's `/results` poll — and [the last section](#what-reads-these-numbers)
says which number each of them quotes.

## Method

- **What ran.** The `compose` job drives four analyses through `curl`
  (`.github/workflows/ci.yml`, the three photograph steps and the retention
  step): a 64×48 flat JPEG the gate refuses as `unusable`; a 1200×1600
  checkerboard with no card in it (`acceptable`, six conditions
  undetermined); a 1200×1600 synthetic card (`good`); and the same card's
  bytes again, so that #39's cache is exercised. The `e2e` job drives two
  browser journeys against its own stack (`apps/web/e2e/`): the anonymous
  journey end to end on the two 1200×1600 fixtures, the only run in CI that
  reaches `completed`; and the unusable-photograph refusal. Six worker runs
  in all, four reaching `awaiting_confirmation`, two `failed` at the gate by
  design, one `completed`.
- **What the photographs are not.** Every fixture is synthetic and at most
  1200×1600 — two megapixels. A phone photograph of a card is 3024×4032, six
  times the pixels the gate has to decode and the detector has to search
  (the corpus in `image-quality-gate-research.md`). No real photograph has
  ever been timed through the pipeline; the budgets below leave room for
  that, and the first measurement on a real photograph is the next section
  someone appends here.
- **How the lines were read.** With the `Logs` step now running on a green
  run too (the CI change in this PR — a dump, not a check), the run's log is
  one download, and the JSON lines are what
  [`development.md`](development.md#logs) describes. The filter used:

  ```bash
  gh run view 34063674946 --log | grep -o '{"[^"]*".*"event": ".*}' | jq -c 'select((.event // "") | test("^(analysis|image|api|economics)\\."))'
  ```

  Locally the same lines come off the stack directly:

  ```bash
  docker compose -f infrastructure/local/docker-compose.yml logs --no-log-prefix api worker
  ```

- **Statistics.** Six runs is not a distribution. A p95 over n = 6 is the
  maximum, so the tables below report **every run** and the maximum, and the
  budgets are stated as p95 because that is what they will be compared
  against once there are runs to take one over. Latencies are on one cold
  runner with one worker process and one client; nothing was contended.

## The eight signals, their budgets, and what was measured

| # | Signal (§67) | Read from | Statistic | Budget | Measured (2026-09-07, six runs) |
| --- | --- | --- | --- | --- | --- |
| 1 | analysis latency | `analysis.step_completed` | Σ `duration_ms` over a run's four steps, p95 | **≤ 10 s** | max **956 ms**; 31 – 956 ms |
| 2 | image-processing latency | `image.assessed` | `duration_ms` per side, p95, split by `cached` | **≤ 3 s** uncached, **≤ 100 ms** cached | uncached max **462 ms**; cached max **14.6 ms** |
| 3 | ML inference latency | `analysis.step_completed` | `duration_ms`, `step` ∈ {`condition`, `grading`}, summed, p95 | **≤ 3 s** | max **235 ms** (227.3 + 7.8) |
| 4 | market-data latency | `market.prices_ingested` (reserved) | `duration_ms` per ingestion run; snapshot age at read | run: **none until measured**; age **≤ 30 days** | **unmeasurable until #54** |
| 5 | failure rates | `analysis.dead_lettered`, `analysis.job_retrying`, `analysis.job_finished` | dead-lettered ÷ `analysis.queued`, same window; retries counted; gate refusals reported | dead-letter **≤ 1 %**; retries **≤ 5 %**; refusals **no budget** | **0 / 6** dead-lettered, **0** retries; 2 / 6 refused by design |
| 6 | provider errors | `api.error` (`code = provider_error`), `analysis.job_retrying` (`error`) | errors ÷ `api.request_completed`, per `route`, same window | **≤ 1 %** per route | **0 / 102** requests; 0 worker errors |
| 7 | model confidence | `analysis.grades_predicted`, `economics.results_computed` | `model_confidence`, `distribution_confidence` per company — a distribution to watch | **no budget** (ADR 0011) | **0.35** for PSA, TAG and BGS on every predicted run; `{}` where all three refused |
| 8 | analysis completion rate | `economics.configuration_recorded` ÷ `analysis.queued` | same window; abandonment is in the denominator | **no floor yet** | **1 / 6** (five stop short by design) |

### 1. Analysis latency

Claim to `awaiting_confirmation`: the worker's run, and nothing the user
does. It is the **sum of a run's four `analysis.step_completed` durations**
(`versions`, `gate`, `condition`, `grading` — the step names are the budget's
keys, and renaming one is a change to this document in the same PR). It is
deliberately not two other numbers that look like it:

- not `created_at → completed_at` on the row — since #244 completion is the
  configuration write, so that span measures the user's think-time at
  `/identify` and `/configure`;
- not `analysis.job_finished`'s `total_ms` — the task's, entry to return,
  which starts one event loop and one engine ahead of the claim and, on a
  worker's first run, carries OpenCV's cold import. On this run that gap was
  315 ms (`compose`, first run: 498 ms total against 183 ms of steps) and
  151 ms (`e2e`, first run: 1 107 ms against 956 ms).

| job | run | `versions` | `gate` | `condition` | `grading` | **Σ steps** | outcome |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| compose | 64×48 flat JPEG | 23.0 | 160.4 | — | — | **183.4** | `failed` (`unusable_photograph`) |
| compose | checkerboard, no card | 13.2 | 121.5 | 2.5 | 2.4 | **139.6** | `awaiting_confirmation` |
| compose | synthetic card | 13.2 | 317.1 | 227.3 | 7.8 | **565.4** | `awaiting_confirmation` |
| compose | same card again | 13.8 | 23.8 | 211.4 | 7.4 | **256.4** | `awaiting_confirmation` |
| e2e | anonymous journey | 16.8 | 754.8 | 181.1 | 3.3 | **956.0** | `awaiting_confirmation` → `completed` |
| e2e | unusable photograph | 10.2 | 21.1 | — | — | **31.3** | `failed` (`unusable_photograph`) |

All in milliseconds. A step that raised has no line, so a run that died is
read off `analysis.dead_lettered` instead — there were none.

**Why 10 s.** `/analyze` already waits 20 × 1 s at the gate before going on
to `/cards` without a verdict (`VERDICT_POLL_ATTEMPTS`), and CI's own poll
ceiling is 30 × 1 s. A budget of half the screen's wait means a run inside
budget always beats the poll, and a run that misses it is visible as the
screen giving up. Against the measured 956 ms on two-megapixel fixtures,
10 s is roughly an order of magnitude of headroom — for the six-fold larger
photograph a phone takes, for a worker handling more than one queue, and
for the trained predictor that replaces the V1 heuristics. It is not tighter
because nothing real has been timed yet; it is not looser because a longer
budget would have to move the screen's wait with it.

### 2. Image-processing latency

`image.assessed`, one line per side, `duration_ms` beside `cached`. A
computed side is the read from object storage, the decode, the detection,
the gate and the warp; a served side is the row and the artifact copy.
Neither includes the artifact upload that follows the line. Both sides run
inside the one `gate` step, so the step's duration is the two sides' sum
plus the uploads.

| job | run | side | `cached` | `duration_ms` | verdict |
| --- | --- | --- | --- | ---: | --- |
| compose | 64×48 flat JPEG | back | false | 153.6 | `unusable` |
| compose | 64×48 flat JPEG | front | true | 4.0 | `unusable` |
| compose | checkerboard | back | false | 115.7 | `acceptable` |
| compose | checkerboard | front | true | 3.3 | `acceptable` |
| compose | synthetic card | back | false | 297.0 | `good` |
| compose | synthetic card | front | true | 9.9 | `good` |
| compose | same card again | back | true | 12.6 | `good` |
| compose | same card again | front | true | 9.1 | `good` |
| e2e | anonymous journey | back | false | 462.1 | `good` |
| e2e | anonymous journey | front | false | 264.2 | `good` |
| e2e | unusable photograph | back | true | 14.6 | `good` |
| e2e | unusable photograph | front | false | 5.0 | `unusable` |

**The cache-hit rate is a count over `cached`**, and on this run it is 6 of
12 — but that figure is the fixtures', not the product's. The `compose` job
uploads the *same bytes* as front and back, so whichever side is processed
second is served from the row the first wrote (#39's cache is
`images.sha256`), and the retention step re-uploads the previous step's card.
In production a hit means the same photograph was uploaded twice; the rate
describes what users do, not how the system is doing, and has **no budget**
— it is reported so that a cache that stopped hitting would be noticed.

**Why 3 s and 100 ms.** The gate is the one step whose cost scales with the
photograph — everything after it works on the fixed 756×1056 artifact — so
it is where a 12-megapixel upload lands. 462 ms for 1200×1600 leaves a
six-fold pixel count inside 3 s with room over; two sides at 3 s is 6 s of
the 10 s analysis budget, which is the gate's share. A served side does no
image work at all, and 100 ms is seven times the slowest one measured.

### 3. ML inference latency

`analysis.step_completed` where `step` is `condition` or `grading`, summed
per run: the four axis analyzers over both artifacts, then the three
per-company predictors over the assessment. Measured 235 ms at most
(227.3 + 7.8), and 4.9 ms on the checkerboard, where the analyzers found
nothing to assess and every predictor refused.

**Why 3 s.** The V1 predictors are arithmetic over an assessment (ADR 0011)
and the analyzers are OpenCV over an artifact of fixed size, so today's
figure barely moves with the input. The budget is not for them. It is for
what replaces them — a trained bundle running on a CPU worker — and 3 s is
the share of the 10 s left after the gate's 6 s and the reads. A model that
needs more than that on a CPU is a model that needs a GPU worker, which is a
deployment decision, not a budget edit.

### 4. Market-data latency

Nothing has ever ingested (ADR 0006 gates the provider on a subscription that
is not active), so there is no figure and there cannot be one until #54
runs. The event name is **reserved as `market.prices_ingested`**, with a
`duration_ms` for the run, so that the first ingestion writes a line this
document can be extended with. Its per-run budget is left unset until then:
a number chosen before the provider has been called once would be a guess
dressed as a budget.

What *can* be budgeted today is the age of what a user is shown.
`stale_after_seconds` on the wire is `Settings.market_stale_after_days`,
30 days, and `/results` says "stale" from that threshold on (#262). A
snapshot older than that at read time is over budget, whatever the ingestion
took — the signal is the snapshot's `created_at` against the read's time,
and it is readable now on every `economics.results_computed` line's
`market_snapshot_id`.

### 5. Failure rates

Three things are called failures and they are budgeted apart:

- **Dead-lettered runs** — `analysis.dead_lettered`, the run that failed
  four times (`MAX_RETRIES = 3`) and was written `failed` with a
  `reason` the photographs did not cause (`job_dead_lettered`,
  `analysis_failed`, `internal_error`, …). Ceiling **1 %** of
  `analysis.queued` over the same window: with jittered backoff between
  attempts, four consecutive failures is a dependency down or a bug, never
  bad luck, and one in a hundred users losing their analysis to it is already
  one too many for a beta that promises nothing else. Measured **0 of 6**.
- **Retried attempts** — `analysis.job_retrying`, the run that failed and
  came back. Ceiling **5 %** of queued: a retry costs the user a 2–8 s
  backoff, not the analysis, and a rate above one in twenty says a dependency
  is flapping. Measured **0**.
- **Refused photographs** — `analysis.job_finished` with `outcome = failed`
  and the row's `unusable_photograph`, the gate doing its job. **No
  ceiling.** The refusal rate is a property of what users photograph and
  of the gate's calibration, and the second is
  [`image-quality-gate-research.md`](image-quality-gate-research.md)'s
  question, decided against real photographs, never against a rate.
  Measured **2 of 6**, both fixtures built to be refused.

The denominator for all three is `analysis.queued` — the `POST
/analyses/{id}/run` that was accepted — because a run that was never queued
cannot fail, and a session that uploaded and walked away is not a failure.

### 6. Provider errors

Two sides. On the HTTP side, `api.error` with `code = provider_error` (the
§66 code every unreachable store answers with — PostgreSQL, the object store,
the job queue, the catalog), against `api.request_completed` on the same
`route`, joined by `request_id`. On the worker side, `analysis.job_retrying`
and `analysis.dead_lettered` carry the exception's `error` type, which names
the dependency (`OperationalError`, `StorageUnavailable`, …). Ceiling **1 %**
of requests per route: the readiness probe degrades on the same conditions,
so a rate above that on a route the probe passed is a partial outage worth a
look.

Measured **0 of 102** requests (65 in `compose`, 37 in `e2e`). The two
`api.error` lines on the run were `image_quality_failure` (409) and
`invalid_image` (400), both the client's and both provoked on purpose. The
`compose` job also stops Redis part-way through; the limiter fails open
(`ratelimit.unavailable`) rather than answering `provider_error`, which is
ADR 0005's decision and why that step produced no error line.

One thing the issue expected and the log does not carry: `api.error` logs
`code` and `status_code`, not the envelope's `details.reason`, so provider
errors cannot be grouped by *which* store was unreachable off the log alone.
Grouping by `route` is the substitute — a route reaches one or two stores.
Adding the reason to the line is code and out of this document's scope; it
is noted here for whoever first needs the finer split.

### 7. Model confidence

`model_confidence` on `analysis.grades_predicted` (per company, at
prediction) and `distribution_confidence` on `economics.results_computed`
(per company, at read). **No budget.** It is the model's statement of its
own certainty, and a target for it would be a number the model is tuned to
report rather than to mean. ADR 0011 declares the V1 heuristics' confidence
at 0.35 — a prior, below #64's provisional 0.50 threshold on purpose, so that
every V1 recommendation is `insufficient_information` — and that is exactly
what was measured: **0.35 for PSA, TAG and BGS** on all four runs that
predicted, `{}` with all three refused on the checkerboard (no card, so no
assessment, so nothing to predict from), and `no_company_can_be_ranked` as
the one `economics.results_computed` line's reason, nothing being priced. The
figure to watch is the distribution of these values once a trained model
replaces the constant; a change in its shape is a model-registry event, not a
budget breach.

### 8. Analysis completion rate

`economics.configuration_recorded` — the write that completes an analysis
(#244) — over `analysis.queued`, same window. The denominator deliberately
includes abandonment: a user who saw the gate's verdict, or the
`/identify` question, and closed the tab is in it. That abandonment is
**invisible by design** — §53 keeps no record of what a user did between
requests, and `/identify` writes nothing until a card is confirmed — so the
gap between queued and completed is the funnel and the failures together,
and only the failures (§5) can be told apart.

**No floor yet.** Measured **1 of 6**, which is the harness's shape, not a
user's: three `compose` runs stop at the confirmation gate because the job
tests the gate, and two runs are refused on purpose. A floor for a beta's
funnel needs a month of real sessions behind it; setting one from CI would
be setting it from a number that measures the test suite. The first month's
figure is the next dated section here, and the floor is chosen then.

### Beside the signals: the API's own latency

Not one of §67's eight, but `api.request_completed` carries a `duration_ms`
on every request and two things downstream depend on it. On this run every
request on an analysis route answered inside **130 ms** (the slowest was a
1200×1600 upload at 129.3 ms; `GET /analyses/{id}`, the poll, at most 57.6
ms and typically under 5 ms; `/readiness` 315 ms, three dependency probes).
Budget **p95 ≤ 250 ms** on the analysis routes, excluding `/readiness`: it
is twice the slowest measured, and it keeps a 1 s poll interval at least
four requests wide, so a client polling on schedule never has two in flight.

## What reads these numbers

- **#272 — the run's time limits.** `task_soft_time_limit` = **60 s**,
  `task_time_limit` = **120 s**. Sixty seconds is six times the analysis
  budget, past `/analyze`'s 20 s wait and CI's 30 s ceiling: a run there is
  not slow, it is stuck, and the soft limit writes `timed_out` through
  `transition(..., failure=)` so the row says so. `RETRY_BACKOFF_MAX_SECONDS`
  is already 60 s — "so a long backoff cannot outlive the analysis" — and the
  two now agree on what that means. The hard limit is the backstop at twice
  the soft one; with `acks_late` and prefork a hard kill acks the message,
  so the row is left for the stall sweep, which treats an analysis
  `identifying` for **≥ 15 min** as stalled — the longest a legitimate run
  can be there is four attempts of 120 s plus three backoffs of at most 60 s,
  eleven minutes. Changing any of these three changes this section in the
  same PR.
- **#271 — the `/results` poll.** First interval **1 s**, backing off to a
  cap of **10 s** (the poll route answers in milliseconds; the cap is for the
  tab left open, not the request). The screen stops polling and says the
  analysis is stuck at **2 min**, the hard limit above: a row still
  `identifying` then has been hard-killed or is between retries, and neither
  is worth a spinner. `stuck` is a screen state, never a §65 state.
- **`/analyze`'s wait** stays at 20 × 1 s (`VERDICT_POLL_ATTEMPTS`): twice
  the analysis budget, so the gate's verdict is on screen for every run
  inside it.
- **Reviewing a run.** A `compose` or `e2e` job's `Logs` step, filtered as
  in [Method](#method), against the table above. A budget missed on one CI
  run is a question, not a failure; missed on three is a new dated section
  here and an issue.

## What this does not decide

No threshold moved, no time limit was set (that is #272, quoting the numbers
above), no assertion was added to CI, and nothing here stands up a metrics
stack. The budgets are first budgets, set against one cold run of synthetic
fixtures: the first real photograph timed through the pipeline, the first
month of beta traffic, and the first trained predictor each earn a new dated
section beneath this one — and a budget moved without a section saying what
was measured is a budget nobody can check.
