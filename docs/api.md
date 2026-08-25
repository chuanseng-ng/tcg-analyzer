# The HTTP API

The service documents itself, so this file does not restate the request and
response shapes. What follows is the part a schema cannot carry: what each
endpoint refuses, and why it refuses it that way.

Run it against a local database and object store:


```bash
uv run uvicorn tcg_api.main:app --reload   # API on http://localhost:8000
```

`GET /health` reports the service status and the application version,
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

The image is built from the repository root, because `services/api` is a member
of the uv workspace and cannot be resolved without it:

```bash
docker build -f infrastructure/docker/api.Dockerfile -t tcg-api:dev .
docker run --rm -p 8000:8000 tcg-api:dev
```

The same image is what the local stack runs, both as the `api` service and as
the one-shot `migrate` service that applies the migrations before the API is
allowed to start.
