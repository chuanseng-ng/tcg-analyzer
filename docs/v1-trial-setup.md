# Running V1 for a trial

This guide covers four things:
- what V1 can be trialled for today;
- how to run it, on your own machine or on a host other people can reach;
- what a tester does;
- what the person running the trial watches.

The production overlay's own reference is
[`infrastructure/deployment/README.md`](../infrastructure/deployment/README.md),
and the decision behind it is
[ADR 0012](adr/0012-the-beta-runs-from-a-compose-overlay.md).

## What a trial can and cannot show

V1 runs end to end, from two photographs to a rendered results screen. **The
advice itself is not live yet.**

**What a tester gets:**
- **Photographs checked for quality.** The front and back pass through the
  quality gate, which names its reason when it refuses one.
- **The card identified.** The tester picks it from the catalog and confirms
  it.
- **A neutral reading of the card's condition:** centering, corners, edges and
  surface. It says "Not measured" where it cannot tell.
- **A full grade distribution for each company** (PSA, TAG and BGS), shown with
  its confidence and the 50% threshold that confidence falls short of.
- **A way to come back.** A return code lets the tester report the grade the
  card actually received, and they choose whether the photographs may be kept
  for training.

**What a tester does not get:**
- **A price.** Market data waits on the provider subscription (#52 → #54).
- **A `grade` or `do_not_grade` recommendation.** Every result is
  `insufficient_information`.
- **A trained grade model.** The three distributions come from declared
  baselines, capped at 35% confidence
  ([ADR 0011](adr/0011-the-v1-grade-predictor-basis.md)).
- **Authentication or counterfeit detection.** Both are outside V1.
- **An account.** Sessions are anonymous and expire after seven days.

So a trial today tests the journey, not the advice. Can a stranger photograph a
card, get it through the gate, find it in the catalog and understand the
results? Tell testers this before they start ([below](#what-to-tell-testers)).

## On your own machine: a look, not a trial

The local stack runs everything with obvious development credentials. It is for
seeing the product, not for letting other people use it.

```bash
docker compose -f infrastructure/local/docker-compose.yml up -d --wait
docker compose -f infrastructure/local/docker-compose.yml exec api tcg-seed-grading-rules
docker compose -f infrastructure/local/docker-compose.yml exec api tcg-seed-catalog
```

Then open <http://localhost:3000>.

**The seed catalog is a 22-card test fixture across four sets**, not the
catalog. It holds Charizard, Blastoise, Venusaur, Pikachu, Mew ex and a few
others, in English and Japanese, so photograph one of those. To search every
card instead, import the full catalog. It reads `api.tcgdex.net` (see
[The database](database.md)); a published catalog version is immutable, so a
later import takes a new version.

```bash
docker compose -f infrastructure/local/docker-compose.yml exec api tcg-import-catalog --version pokemon-catalog-tcgdex-v0.1.0 --language en --language ja
```

`down -v` on the local stack destroys its database and every photograph. Use
`down` to stop it.

## On a host, for other testers

**What the host needs:**
- **Docker Engine 26 or later, with Compose v2.** The worker's isolation relies
  on internal networks getting no external DNS, which Docker 26 introduced.
- **A domain whose DNS points at the host,** with ports 80 and 443 (TCP and
  UDP) reachable. Port 80 has to be reachable for the public certificate.
- **Roughly 4 GB of memory.** This is an estimate: idle, the whole stack
  measured about 0.7 GB, and the worker is allowed up to 2 GB.

```bash
git clone https://github.com/chuanseng-ng/tcg-analyzer.git
cd tcg-analyzer
cp infrastructure/deployment/.env.example infrastructure/deployment/.env
```

Fill in `infrastructure/deployment/.env`:
- `TCG_DOMAIN` is the domain.
- Every secret is `openssl rand -hex 32`.

The overlay refuses to start while any required value is missing. Then bring
the stack up, seed the grading rules and import the catalog:

```bash
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml up -d --wait
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml exec api tcg-seed-grading-rules
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml run --rm catalog-import --version pokemon-catalog-tcgdex-v0.1.0 --language en --language ja
```

The import is a service of its own, because it is the one thing that needs the
internet and the API deliberately has no route out.

**Smoke check.** Readiness should report `database`, `storage` and `redis` all
`ok`. The site itself is `https://<your domain>/`.

```bash
curl -fsS https://beta.example.com/api/readiness
```

**The first run on a real domain is the first time the overlay obtains a
public certificate.** CI and the local verification ran on `localhost`, with
Caddy's internal certificate authority. If the site does not answer over
HTTPS, read the proxy's log first:

```bash
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml logs proxy
```

## What testers do

1. **`/`**: *Analyze a card*.
2. **`/analyze`**: take the front and the back.
   - The tester is asked whether these photographs may be kept to train future
     models. The question is per analysis and optional.
   - *Use these photographs* uploads them, and the quality gate's verdict
     follows.
3. **`/cards`**: *Choose which card this is* runs the analysis. The tester
   searches the catalog and picks the card.
4. **`/identify`**: confirm the card. Nothing is graded until a person has said
   which card it is.
5. **`/configure`**: the costs the tester knows. A blank is recorded as not
   known, never as zero.
6. **`/results`**, in this order:
   1. the recommendation, which today says there is not enough information,
      and why, with the figure and the threshold;
   2. the figures;
   3. the offer of a return code, shown once;
   4. the three grade distributions;
   5. the comparison between companies;
   6. the condition reading.
7. **Later:**
   - `/feedback/<code>` reports the grade the card actually received.
   - `/consent` withdraws consent to keep the photographs, using the
     withdrawal code.

**Photographs that pass the gate:**
- the whole card in frame, on a plain background that contrasts with its
  border;
- even light with no glare across the surface;
- sharp focus;
- the card filling a good part of the frame.

The gate names what it refuses, so a refusal tells the tester what to retake.

### What to tell testers

> This is a trial of an app that estimates a trading card's condition and its
> likely grades. It is not an official grading service and does not
> authenticate cards; every grade it shows is a probability, never a promise.
> Prices are not connected yet, so it will not tell you whether grading is
> worth it — it will say there is not enough information, and that is
> expected. Your photographs are deleted after seven days unless you choose
> to let us keep them for training. If you get a return code at the end, keep
> it: it is the only way to tell us, later, what grade your card really got.

## Running the trial

**Logs.** Every line is JSON. [Observability](observability.md) names the
events and the budget each one is held to:
- `analysis.step_completed` carries a duration;
- `analysis.dead_lettered` marks a run that failed for good;
- `api.error` carries a code.

```bash
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml logs -f api worker
```

**Reported grades** wait for a person to review them:

```bash
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml exec api tcg-review-grade-feedback --pending
docker compose --env-file infrastructure/deployment/.env -f infrastructure/local/docker-compose.yml -f infrastructure/deployment/docker-compose.prod.yml exec api tcg-review-grade-feedback --feedback-id <id> --decision validated
```

A validated report is a label with no photograph attached. It trains nothing by
itself; see [Training and testing the models](model-training-and-testing.md).

**Consented photographs** are copied into this host's `training_images` table
as [ADR 0008](adr/0008-permitted-training-image-sources.md)'s fourth approved
class. They carry no grade and no identified card. They are the host
database's, not the development corpus in `tcg_corpus`.

**Retention** is [Retention and expiry](retention.md)'s:
- Photographs and analyses go after seven days, in an hourly sweep.
- A reported grade is kept for 180 days.
- A consented copy is kept until it is withdrawn.

**The rate limit** is 30 writes a minute per client address. A group testing
from behind one office or carrier address shares that one bucket. The overlay
does not pass `TCG_API_RATE_LIMIT_REQUESTS` through today, so raising it means
adding that variable to the `api` service's environment in the overlay.

**Updating and stopping:**
- **Update:** `git pull`, then the `up` command above with `--build`, so the
  images are rebuilt from the new source.
- **Stop:** `down` stops the stack and keeps everything.
- **Never `down -v` on the host.** It destroys the database, every photograph,
  the broker's certificate and Caddy's certificates.

## Known limits of a trial today

- **No prices and no verdict.** Every result is `insufficient_information`
  (#54, ADR 0011).
- **Grade distributions are uncalibrated baselines,** not models trained on
  graded cards.
- **The annotation tool is off on the host.** It belongs to the local stack
  (ADR 0009, ADR 0012).
- **No backups and no monitoring beyond the logs.** The state is the
  `postgres-data` and `minio-data` volumes, and backups are the later platform
  decision.
- **One worker, with two analyses in flight at a time.** That is ample for a
  trial: a run's steps measured under a second in CI, against a 10 s budget.
