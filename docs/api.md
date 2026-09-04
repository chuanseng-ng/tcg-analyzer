# The HTTP API

The service documents itself, so this file does not restate the request and
response shapes. What follows is the part a schema cannot carry: what each
endpoint refuses, and why it refuses it that way.

Run it against a local database and object store:


```bash
uv run uvicorn tcg_api.main:app --reload   # API on http://localhost:8000
```

`GET /health` reports the service status and the application version. It is
**dependency-free by design** and must stay that way: it answers whether the
process is alive, so acquiring a database or network dependency there would make
a liveness probe fail for a reason liveness is not about. Dependency checks belong
to `GET /readiness`, which answers 200 when the service can serve traffic and 503
with `checks.database = "unavailable"` when it cannot.

`GET /catalog/version` reports which card catalog the deployment is serving,
`GET /cards/search` finds cards in it, and
`GET /cards/{id}` returns the canonical detail for one card — its name, set, card
number, language, rarity, variant and the external database identifiers recorded
for it. An identifier naming no card answers 404 under the spec §66 taxonomy; it
carries no prices and no card images.

`GET /cards/search` filters on `text`, `game`, `language`, `set_code`,
`card_number` and `variant`, all optional and ANDed, and pages with `limit` and
`offset`. `text` matches a fragment of the printed name without regard to case
and works for Japanese; `card_number` matches as a prefix, so `25`, `025` and
`025/165` all find the card printed `025/165`. Results are ordered by
`(set_code, card_number, variant, id)` — a total order, so paging neither drops
nor duplicates a row. Nothing matching is an empty page, never a 404.

`GET /grading-companies` lists the grading companies the product supports, each
with the exact grades it can issue and the version of its published standard in
force today (spec §23), so a result can be tied back to it. **Render the scale
from what this endpoint returns rather than hard-coding one**: PSA and TAG issue
eighteen grades and no 9.5, BGS issues nineteen and has one, and all three issue
half grades everywhere else — a picker built on a single shared scale misrenders
one of them, and refuses a PSA 8.5. A company added after V1 appears here with no
frontend change at all. The response is slow-moving reference data and says so
with `Cache-Control: public, max-age=3600`. It carries no grading fees: spec §45
makes those configurable economic inputs, so they belong to the economic engine's
configuration rather than to a table here that would go stale quarterly.

`GET /cards/{id}/market` returns what one card is worth: the ungraded price and
every grade every supported company can issue, each carrying spec §38's
`price_confidence` and `price_age`. **Per price, not per response** — a card can
have a raw price from this morning and a PSA 10 price from six weeks ago, and one
number for the pair would hide exactly the gap a user needs to see. Both are
computed when the request arrives rather than stored, which is why this response
alone is sent `Cache-Control: no-store`: a cached body would report an age frozen
at the moment it was built, and spec §38 forbids presenting stale data without
identifying it. Confidence is flat at the provider's own figure for a day, then
falls to a floor above zero at `TCG_API_MARKET_STALE_AFTER_DAYS`; the floor is
above zero because an old price on a thinly traded card is still the only
evidence there is, and reporting it at zero would be indistinguishable from
having none.

**No provider is called during the request** (spec §37). Everything comes from a
market snapshot ingested out of band, and the snapshot's identifier and
`data_version` come back with it — show the date beside the prices, because a
record of a past market is honest where the same figures presented as current are
not. Pass `?snapshot_id=` to re-read exactly what a past analysis saw. A price the
snapshot does not hold is `null` rather than absent, never filled in from another
company and never interpolated: TAG carries no prices at all in V1, and the
response says so eighteen times rather than borrowing PSA's. A price of `0.00` is
a real observation about a card nobody will pay for and is emphatically not the
same thing. Nothing has been ingested yet, so today every deployment answers 503
`market_data_unavailable`; that is also the answer for a `?snapshot_id=` naming a
snapshot that was never generated, at 404, since the request was well formed and
the deployment simply holds no such cut. There is **no price history and no
endpoint that lists snapshots**, deliberately — see
[ADR 0006](adr/0006-the-v1-market-data-provider.md), whose redistribution test is
functional rather than formal.

`POST /analyses` starts an analysis. There is no login and no registration:
V1 identifies a user by an anonymous session token only (spec §53), returned in
an HTTP-only cookie that every later call must carry. `GET /analyses/{id}`
reports the state of one, but only to the session that started it — an unknown
identifier, another session's analysis, a missing cookie and an expired one all
answer 404 with the same body, so the endpoint cannot be used to discover which
analyses exist. Sessions expire after `TCG_API_SESSION_TTL_SECONDS`; nothing
about the caller is recorded.

`POST /analyses/{id}/images?side=front` uploads one photograph, as the raw
bytes of the request body rather than as a multipart form. That is deliberate:
**no client filename ever arrives**, so spec §55's "sanitize filenames" and
"generate server-side storage paths" are satisfied by construction rather than
by validation, and the byte limit can be applied while the upload is still
arriving. Uploaded images are untrusted input (spec §56), so the file is
accepted only if its **content** — never the type it declares — is a JPEG or a
PNG, if it is within `TCG_API_UPLOAD_MAX_BYTES` and `TCG_API_UPLOAD_MAX_PIXELS`,
and if it decodes. The pixel limit is separate from the byte limit because a
two-kilobyte PNG can declare a bitmap of eleven gigabytes, and it is checked
from the file's header before anything is decoded. EXIF — including GPS — is
removed before the image is stored (spec §54), losslessly: a JPEG is rebuilt
from its own marker segments with the scan copied byte for byte, so the fine
surface detail the condition models read is never re-compressed. The digest
recorded on the row is of the bytes that were stored, not of the bytes that
arrived. A second upload for the same side replaces the first, and the object it
replaced is deleted. The analysis reaches `uploading` on the first photograph
and `uploaded` once both sides have arrived, which is the one state
`POST /analyses/{id}/run` may start from. Every rejection is `invalid_image`
with a message naming the rule and nothing about the decoder.

`POST /analyses/{id}/confirm-card` records which card the user is holding
(spec §20). The card identifier arrives from a client and is therefore not
trusted (spec §55): it is resolved against the catalog before it is written, and
one naming no card is refused with the same `card_not_identified` that
`GET /cards/{id}` answers with. Only an analysis in `awaiting_confirmation` may
take a confirmation, and recording one moves it to `analyzing`. Spec §65's
states move forwards only, so there is no second confirmation and no changing
the card afterwards — both are a 409, and a card chosen in error is corrected by
starting a new analysis.

`POST /analyses/{id}/economic-configuration` records the economics of the
decision (spec §45, §46, §43): the six cost line items, the optional acquisition
cost, the companies to compare and the optimization mode. **Every cost field is
optional and the endpoint fills it from the engine's own defaults**, so no
frontend carries a second copy of them — and the amounts that were actually used
come back on the 201, which is where a screen reads them from.

**An absent acquisition cost is not zero.** Omitting it is reported later as
`acquisition_cost_not_supplied`, while `"0.00"` is a real acquisition cost
somebody typed; collapsing the two would answer spec §45's second question with a
figure nobody supplied. Amounts are decimal **strings** in both directions and a
JSON number is refused, because binary floating point cannot represent money the
domain quantises to the cent.

A configuration is accepted **only while the analysis is `analyzing`** — spec
§5's position for this step, which `confirm-card` is what reaches — and **exactly
once**. A second submission is a 409, and the arbiter is a conditional `UPDATE`
rather than the state read before it, so two concurrent submissions cannot both
win. Spec §44's five recommendation thresholds are **stored and reported but
never accepted**: no client gets to gate its own recommendation. A malformed
request — a negative amount, a selling-fee rate outside `[0, 1]`, an unknown
company, an unknown mode — is FastAPI's own 422, validated by the economic engine
itself; spec §66 has no code for a malformed request and a ninth is not invented
for one.

`GET /analyses/{id}/results` returns the economics and the recommendation (spec
§41, §44, §49). **The two spec §41 figures are separately named and share no
field name**: `incremental_grading_decision` answers "should I grade the card I
own?" and `investment_return` answers "did buying it to grade make money?", with
`incremental_roi` and `investment_roi` beside them. **Nothing is called `roi`
alone and nothing is a cost total** — the two questions have different
denominators ([ADR 0007](adr/0007-roi-and-the-capital-at-risk-basis.md)), and a
shared field name is the conflation §41 forbids. Each figure is present-and-null
beside its own reason string rather than absent, so a client cannot mistake "we
could not compute this" for "we did not try".

The **full** grade distribution travels (spec §2.1), never only an expected
grade, and `reason` is `code`/`figure`/`value`/`threshold` with no message
field — spec §50 forbids an explanation unrelated to the evidence, so the copy is
the frontend's to write from those four fields. The response echoes the analysis's
own spec §57 record, including the market snapshot it was computed against, which
is what ADR 0006 requires the UI to date-stamp. `Cache-Control: no-store`, for
the same reason `GET /cards/{id}/market` is.

**`companies` and `recommendation` fill in from what the worker stored** (#228).
Once an analysis has an economic configuration and the worker has recorded its
grade predictions, `companies` carries one entry per configured company whose
model predicted — the stored distribution, never one predicted at read time —
priced against the snapshot the analysis recorded, and `recommendation` is the
engine's answer under the stored thresholds, with the weakest photograph's
quality score as spec §44's third confidence source. Until then both are `[]`
and `null`, and that `null` is deliberately not spec §44's
`insufficient_information`: the engine has not declined to recommend, it has
not been asked. A deployment that has never ingested (ADR 0006) still answers
200: every priced figure is present-and-null beside the engine's own reason,
and the recommendation is `insufficient_information` with
`comparison_reason: no_company_can_be_ranked`. A company whose model refused has
no distribution to carry and is not a `companies` entry; it appears in
`recommendation.comparison.unranked` with its stored reason — except when every
configured company refused, where the engine's `no_company_can_be_ranked` is the
whole answer and the per-company reasons are not on the wire.

With the V1 heuristic predictors every recommendation is
`insufficient_information` on `grade_confidence_below_threshold`: ADR 0011's
declared confidence of 0.35 sits below the provisional `minimum_grade_confidence`
of 0.50, and the response says so with the value and the threshold rather than
forcing a verdict.

The four writes — `POST /analyses`, `POST /analyses/{id}/images`,
`POST /analyses/{id}/confirm-card` and
`POST /analyses/{id}/run` — are rate-limited
per client address (spec §55, which names analysis endpoints *and* image
uploads), `TCG_API_RATE_LIMIT_REQUESTS` per
`TCG_API_RATE_LIMIT_WINDOW_SECONDS`, counted in the same Redis the job queue
runs on so the limit holds across replicas. A throttled request is a 429
carrying `Retry-After`, deliberately outside the spec §66 error envelope —
see [ADR 0005](adr/0005-rate-limiting-the-analysis-endpoints.md). Polling
`GET /analyses/{id}` is not limited, and neither are the catalog reads. With
`TCG_API_REDIS_URL` unset, or Redis unreachable, the limiter lets requests
through rather than refusing them. The OpenAPI schema is at `/openapi.json`
and the interactive documentation at `/docs`. Settings are read from `TCG_API_`-prefixed environment variables or
from `.env` — see [Configuration](../README.md#configuration).

## Which store failed

Spec §66's taxonomy is closed at eight codes, and `provider_error` is the one a
store's unavailability raises. Eight codes cannot say *which* store was down, so
`details.reason` does — and each one is deliberately distinct, because the whole
point of the field is that an operator reading a log can tell a Redis outage from
a Postgres outage without correlating timestamps.

| `details.reason` | Raised when |
| --- | --- |
| `catalog_unreachable` | the catalog reads (`/catalog/version`, `/cards/*`, and `confirm-card`, which resolves against it) |
| `no_catalog_version_registered` | `/catalog/version` with an empty `card_database_versions` |
| `grading_rules_unreachable` | `/grading-companies` — the 503 carries no cache header |
| `analysis_store_unreachable` | any analysis read or write |
| `image_store_unreachable` | the object store, on upload and on the annotation bytes route |
| `job_queue_unreachable` | `POST /analyses/{id}/run` with Redis down or unset — deliberately not the analysis store's reason |
| `market_store_unreachable` | `/cards/{id}/market` — deliberately not `market_data_unreachable`, since the same route also raises §66's `market_data_unavailable`, and the two must stay unmistakable in a log |
| `economic_configuration_store_unreachable` | `POST /analyses/{id}/economic-configuration` |
| `dataset_store_unreachable` | the `/internal/annotation` routes |
| `stored_object_missing` | an annotation row naming bytes the store does not hold — a **500** `internal_error`, not a 503, because two stores disagreeing will not come right on a retry |

Caching follows from what a body claims rather than from how expensive it was to
build. `GET /grading-companies` is `public, max-age=3600` — slow-moving reference
data. `GET /cards/{id}/market` and `GET /analyses/{id}/results` are `no-store`,
because both report a freshness figure computed at the moment of asking, and a
cached body would report an age frozen when it was built. The annotation bytes
are `private, no-store`. Everything else sends no cache header at all.

`/internal/annotation` is **not part of spec §64**. §64's endpoints are the
consumer product; this is the internal surface `apps/annotation` reads — a work
list of training images nobody has annotated, one image with the other views of
the same physical copy, and the bytes themselves. It lives in this application
because spec §7 says not to create unnecessary microservices in V1, and a second
FastAPI application would duplicate the session, error-envelope and migration
wiring in order to enforce a boundary the deployment already enforces
([ADR 0009](adr/0009-the-dataset-store-as-a-database-domain.md)). It appears in
this OpenAPI schema because that is the only sanctioned way a TypeScript
application learns an API shape
([ADR 0001](adr/0001-language-boundaries-in-the-monorepo.md)). **Neither of those
is what keeps it internal**: the `/internal` prefix is what an ingress rule
matches, and the tool is not routable from the public origin.

An image is *awaiting annotation* when it carries neither a defect marker nor a
centering measurement. Both tables are checked rather than one, because spec
§30's eleven features are split across two of them — an image somebody has
measured but not marked has been worked on, and putting it back in the queue
would invite a second, contradictory reading of the same card. The total falls
as annotations land, so a page boundary moves under a client that is annotating
while it pages; that is stated in the schema rather than hidden.

The bytes come back from `…/bytes`, which reads them through ADR 0002's
`ObjectStorage` port and streams them, with `Cache-Control: private, no-store`.
**The service streams rather than minting a signed URL, which is a deliberate
departure from what the issue asked for.** Two reasons: a presigned URL names the
host the *service* reaches the store on, which inside the local Compose network
is `minio:9000` and resolves for nobody with a browser; and a signed URL is a
bearer credential nobody can revoke, which is a poor thing to hand out for a
training photograph when ADR 0008 makes withdrawal a right the corpus has to
honour. Streaming from a route already behind its own ingress is less to get
wrong. Nothing outside `packages/shared` has ever called `signed_download_url`,
and this did not change that.

`POST /internal/annotation/images/{id}/annotations` records one annotator's work
on one image, in one transaction. **One image per request**, and that is the rule
rather than a convenience: a marker belongs to the image whose artifact its
coordinates are fractions of, and the viewer can be showing the other side of the
same physical copy. **Append-only**, so there is no edit endpoint and there will
not be one — `trg_image_annotations_immutable` refuses an `UPDATE`, a correction
is a new annotation, and the current view of a corner is the newest row for it.

**The annotator and the timestamp are the service's.** Spec §30 asks that both be
recorded automatically rather than typed, so the request carries neither: the
annotator comes from `TCG_API_ANNOTATOR_ID` and the timestamp from the row's
default. Sending an `annotator_id` is **refused rather than ignored**, because a
client that believes it set one is worse off than a client that is told it cannot
— and spec §53's restraint, which the column's `^[a-z0-9][a-z0-9_-]*$` CHECK makes
structural, is not something anybody should be able to think they circumvented.

The three marker kinds are three request shapes discriminated on `kind`, so
§14's, §15's and §16's label lists reach the OpenAPI document — and therefore
`apps/annotation` — as three separate vocabularies. An edge can be `rough_cut`
and a corner cannot; a surface names no region at all, because §16 names no
positions and a surface defect's position is its bounding box. Only the surface
shape carries `representation` — which frame its coordinates are fractions of,
`normalized` or `original` — required with no default; a corner or edge naming
one is refused, since theirs is always the artifact (#175, ADR 0010).

**Claims about the artifact need the artifact.** A centering ratio, a corner or
edge bounding box, and a surface annotation declaring `normalized` are all
claims about the standardized artifact, so against a photograph no card was
located in they mean nothing: sending one for an image whose `has_artifact` is
false is a **409**. A corner or edge marker with no box is still accepted there,
because its region names its position — refusing the whole request would strand
such an image at the head of the work list for ever — and so is any surface
marker declaring `original`: the photograph always exists, and ADR 0010 makes it
the one frame that resolves §16's fine defect classes (#175). An annotation
recording nothing at all is a 422, since it would take the image off the work
list having said nothing.

`GET /internal/annotation/images/{id}` reports every annotation and measurement
already recorded, oldest first and **not collapsed to a current reading**: a
surface has as many defects as it has, so no one rule fits all three kinds. The
work list excludes an annotated image, so this endpoint is the only way one is
seen again.

`?representation=normalized` is the standardized artifact an annotation's
coordinates are fractions of; `?representation=original` is the photograph. Asking
for an artifact that was never stored is a **404 rather than a substitution**: the
detail endpoint has already said which representation exists, and quietly serving
the photograph instead would hand a client a frame whose coordinates mean nothing.
An unknown image is a bare 404 outside the §66 envelope, on
`GET /analyses/{id}`'s reasoning — none of the eight codes means "not found", and
the taxonomy stays closed at eight. A row naming bytes the store does not hold is
a 500 `internal_error` with `details.reason` of `stored_object_missing`, not a
503: two stores disagreeing will not come right on a retry.

The image is built from the repository root, because `services/api` is a member
of the uv workspace and cannot be resolved without it:

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker run --rm -p 8000:8000 tcg-api:dev
```

The same image is what the local stack runs, both as the `api` service and as
the one-shot `migrate` service that applies the migrations before the API is
allowed to start.
