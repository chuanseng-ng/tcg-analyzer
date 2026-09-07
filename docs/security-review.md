# Reviewing spec §55 and §56 against the tree

- Date: 2026-09-07
- Refs: M10, #263, spec §54, §55, §56, §77, ADR 0002, 0003, 0005, 0008,
  0009; #269, #272, #273, #284, #285
- Reviewed: commit `0d9b918` (the `main` this branch left), the whole tree —
  `services/api`, `packages/shared`, `ml/*`, `infrastructure/`, `.github/`
- Verdicts: **11 met · 3 not met · 2 decided unnecessary** over the sixteen
  bullets

Spec §55 lists twelve minimum security requirements and §56 four for the ML
container. Each has been answered somewhere — a module docstring, a
Dockerfile comment, an ADR, a sentence in [`api.md`](api.md) — and nowhere
all at once, so until now nothing existed that a reviewer could sign. This
document is that record, made the way
[`image-quality-gate-research.md`](image-quality-gate-research.md) recorded
the gate's first measurement and [`observability.md`](observability.md) the
first latency figures: **a dated reading of the tree at one commit, one
verdict per bullet, every verdict with evidence a reader can chase.** It is
not a living table. Code moves under these line numbers; when enough has
moved that the verdicts need re-reading, the next review is a new dated
section beneath this one, never an edit of it.

A verdict is one of three words:

- **met** — the requirement holds in the tree, and the evidence is a
  `path:line` and, where one exists, the test that would fail if it stopped
  holding. A ceiling or a gap noted beside a *met* is a fact about the
  edge, not a softened verdict.
- **not met** — it does not hold, and the issue that closes it is named.
  Every *not met* here is #273's, the production Compose overlay, because
  every one of them is a property of how the containers are run rather than
  of what they run.
- **decided unnecessary** — the requirement as the spec words it is not the
  right control for this system, the reason is written down, and so is the
  condition that re-opens the decision.

## Method

The review read the source, not a running system. What was read, and what
the citations point into:

| Area | Files |
| --- | --- |
| Upload validation | [`image_validation.py`](../services/api/src/tcg_api/analysis/image_validation.py), [`routers/analyses.py`](../services/api/src/tcg_api/routers/analyses.py), [`config.py`](../services/api/src/tcg_api/config.py) |
| Storage keys and the port | [`storage/keys.py`](../packages/shared/src/tcg_shared/storage/keys.py), [`storage/port.py`](../packages/shared/src/tcg_shared/storage/port.py), [`storage/s3.py`](../packages/shared/src/tcg_shared/storage/s3.py), [`tcg_api/storage.py`](../services/api/src/tcg_api/storage.py) |
| The internal surface | [`routers/annotation.py`](../services/api/src/tcg_api/routers/annotation.py), [`datasets/annotation.py`](../services/api/src/tcg_api/datasets/annotation.py), [ADR 0009](adr/0009-the-dataset-store-as-a-database-domain.md), [`apps/annotation/README.md`](../apps/annotation/README.md) |
| Rate limiting | [`rate_limit.py`](../services/api/src/tcg_api/rate_limit.py), [ADR 0005](adr/0005-rate-limiting-the-analysis-endpoints.md), [`routers/economics.py`](../services/api/src/tcg_api/routers/economics.py) |
| The worker | [`analysis/jobs.py`](../services/api/src/tcg_api/analysis/jobs.py), [`analysis/quality.py`](../services/api/src/tcg_api/analysis/quality.py), [`analysis/condition.py`](../services/api/src/tcg_api/analysis/condition.py), every `ml/*/src` |
| Containers | [`worker.Dockerfile`](../infrastructure/docker/worker.Dockerfile), [`api.Dockerfile`](../infrastructure/docker/api.Dockerfile), [`docker-compose.yml`](../infrastructure/local/docker-compose.yml), [ADR 0003](adr/0003-the-local-development-stack.md), [`tests/test_compose_stack.py`](../tests/test_compose_stack.py) |
| Logging and secrets | [`logging.py`](../services/api/src/tcg_api/logging.py), [`.env.example`](../.env.example), [`.gitignore`](../.gitignore), [`.dockerignore`](../.dockerignore) |
| CI | [`ci.yml`](../.github/workflows/ci.yml), [`codeql.yml`](../.github/workflows/codeql.yml), [`dependabot.yml`](../.github/dependabot.yml) |

Paths below are repository-relative; `api/` abbreviates
`services/api/src/tcg_api/` and `shared/` abbreviates
`packages/shared/src/tcg_shared/`. Test names are under
`services/api/tests/` unless a path says otherwise.

What was **not** done, so that nobody reads more into the verdicts than they
carry: no penetration test, no fuzzing of the decoders, no dynamic scan of
a running stack, and no dependency audit — the audit is #284, and this
review found that nothing runs one. Every grep for a dangerous primitive
(`subprocess`, `pickle`, `eval`, `exec`, `tempfile`, `cv2.imread`,
`torch.load`, an HTTP client) was run over `services/api/src` and `ml/`
excluding `.venv`, and every hit is accounted for below, false positives
included.

## §55 — the twelve minimum requirements

### 1. Validate image MIME type — met

The type is what Pillow decodes the bytes as, never what the request says.
`validate_image(data, *, max_pixels)` (`api/analysis/image_validation.py:116`)
takes no header; the sniff is `Image.open(BytesIO(data))` at `:135`, the
format is read back at `:148`, and the accepted set is the closed
`_MIME_TYPES` at `:65` — JPEG and PNG, with WebP, TIFF, HEIC and the rest
refused by omission (`:40-43` says why). The upload route reads no
`Content-Type` at all; what OpenAPI advertises (`api/routers/analyses.py:1166-1174`)
is a declaration, and the response field disclaims it outright (`:485-490`,
*"Never the type the request declared"*). `images.mime_type` is written from
the sniff (`:1296`).

Pinned by `test_image_validation.py`: `test_the_accepted_set_is_closed:110`,
`test_a_shell_script_is_not_an_image:115` (the body is
`#!/bin/sh\nrm -rf /`), `test_image_magic_bytes_alone_do_not_make_an_image:125`
— a JPEG prefix on non-image bytes is refused, which is what proves the
sniff is a decode and not a prefix compare — and
`test_other_real_image_formats_are_refused:132` over WEBP, TIFF, GIF, BMP.

### 2. Validate file size — met, with two documented ceilings

Two limits, both read before the whole body exists. The byte cap is
streamed: `_read_body` (`api/routers/analyses.py:1089-1107`) reads
`request.stream()` in chunks and trips at `:1101` when the running total
passes `Settings.upload_max_bytes` (15 MiB, `api/config.py:284`);
`Content-Length` is explicitly not consulted (`:1094-1096` — *"the client's
claim about the body, and a chunked request does not carry one at all"*).
The pixel cap is the decompression-bomb defence: `image.width * image.height`
is compared against `upload_max_pixels` (50 MP, `config.py:292`) at
`image_validation.py:153`, **before** `image.load()` at `:157`, and `:152`
says that ordering is the whole defence; Pillow's own
`DecompressionBombError` at `open()` maps to the same refusal (`:138-142`).

Pinned by `test_a_decompression_bomb_is_refused_without_being_decoded:180`
(a sub-kilobyte PNG declaring 60 000 × 60 000, refused in under a second,
so no bitmap was allocated), `test_an_image_exactly_at_the_limit_is_accepted:201`,
`test_a_truncated_jpeg_is_refused:210`, and at the endpoint
`test_image_upload_endpoint.py:522` (`test_an_oversized_upload_is_refused`,
asserting the store received nothing) and `:540`.

Two ceilings are written in the code and are restated here so that they are
on the record rather than in a comment:

- **No per-axis dimension cap.** `:153` is the module's only dimension test
  and it is the product; a 1 × 50 000 000 image passes it. Pillow's default
  `MAX_IMAGE_PIXELS` (≈ 89 MP) is also a product. Nothing downstream
  allocates on an axis alone, so this is a ceiling, not a hole.
- **The JPEG strip is a marker walk, not a parser.** `_strip_jpeg`
  (`:199-256`) drops the metadata segments (`_DROPPED` at `:194`: APP1,
  APP13, COM) and copies everything from the first SOS onward verbatim
  (`:246-249`), so a trailer appended after EOI, or an APP segment placed
  between the scans of a progressive image, survives. The `ponytail:` at
  `:207-211` names the upgrade path: a real parser, *"only if a camera is
  found that puts anything personal there"*. This is a §54 (privacy)
  ceiling, and §12 below says what it means for content scanning.

Neither default is pinned by a test — `test_config.py` names neither
setting — which is noted, not filed: the values are provisional and a test
would only pin the guess.

### 3. Sanitize filenames — met, by never accepting one

There is no filename to sanitise. `upload_image`
(`api/routers/analyses.py:1211-1223`) takes the analysis id from the path,
the side from a query enum, and the body raw (`:1258`); no `UploadFile`, no
`File(...)`, no `Form(...)`, no multipart. [`api.md`](api.md) says so at
`:105-109`. The one filename-shaped helper in the repository,
`sanitise_filename` (`shared/storage/keys.py:131-155`), is exported and
called by nothing outside its own tests. The operator CLI that ingests
corpus photographs (`api/datasets/ingestion.py:364-365`) takes `--front` /
`--back` as paths on the operator's own disk, reads them (`:505`) and still
names the object with `generate_key` (`:295`) — the local name is never
stored.

Pinned by `test_image_upload_endpoint.py:304`
(`test_nothing_the_client_sends_can_influence_the_key`), which sends
`Content-Disposition: attachment; filename="../../../etc/passwd"` **and**
`X-Filename: ../../../etc/passwd` and asserts a 201 whose key contains
neither; and `:335`, where a traversal in `side` is a 422.

### 4. Generate server-side storage paths — met

`generate_key(namespace)` (`shared/storage/keys.py:106`) is the only way a
key is minted: `{namespace}/{YYYY}/{MM}/{DD}/{uuid4}` at `:128`, the
namespace a single lowercase slug (`:122-125`), and the signature takes no
filename — `test_storage_keys.py:108-118` asserts that structurally, with
`inspect.signature`. Every key that is *read back* goes through
`StorageKey.__post_init__` (`:84-100`), whose pattern (`:52`, explained at
`:46-51`) refuses a leading or trailing slash, `//`, `.` and `..` segments,
backslashes, whitespace, control characters, percent-encoding and non-ASCII;
`packages/shared/tests/test_storage_keys.py:38-59` parametrizes sixteen of
those, including a Windows traversal, a percent-encoded `..`, a null byte
and a drive letter. Three namespaces exist: `uploads`
(`api/routers/analyses.py:1277`), `normalized` (`api/analysis/quality.py:80`)
and the dataset ingest's own (`api/datasets/ingestion.py:295`).

### 5. Never trust client-side card metadata — met

`CardConfirmationRequest` (`api/routers/analyses.py:452-465`) has one field,
`card_id: UUID`, and its docstring says why a name, a set or a variant would
be *"something this service must not believe (spec §55)"*. The id is
resolved against the catalog before anything is written (`:1006`), an
unknown card is a 404 `card_not_identified` (`:1011-1017`), and only then
does the transition and `set_confirmed_card` run in one transaction
(`:1020-1027`). `set_confirmed_card` writes `card_id` and nothing else
(`api/analysis/sessions.py:272-298`; the `RESTRICT` FK is the backstop, not
the check). No request model anywhere in the router accepts a confidence, a
score or a printed field.

Pinned by `test_analyses_endpoint.py:1034`
(`test_a_card_the_catalog_does_not_hold_is_refused`, quoting §55), which
asserts the 404, that nothing was written and that the state did not move.

### 6. Rate-limit analysis endpoints — met; one determination for #269

[`rate_limit.py`](../services/api/src/tcg_api/rate_limit.py) is ADR 0005
made code: a fixed window of `INCR` then `EXPIRE … NX` in one transaction
(`:137-150`; the `ponytail:` at `:126-128` names the sliding window as the
upgrade), on the Redis the job queue already runs on (`:83-96`), 30 requests
per 60 s (`api/config.py:258,268`). A throttled request is a bare 429 with
`Retry-After`, outside the §66 envelope (`:163-167`; ADR 0005:40-49 fixes
that, and `test_rate_limit.py:178` holds the taxonomy at eight codes).
**Five** routes carry `Depends(analysis_rate_limit)` and share one bucket:
`POST /analyses` (`api/routers/analyses.py:707`), `/run` (`:826`),
`/confirm-card` (`:917`), `/images` (`:1149`) and `/economic-configuration`
(`api/routers/economics.py:1534`). Reads are deliberately unlimited —
`GET /analyses/{id}` because §65 requires polling (ADR 0005:58-59), the
catalog because it is the search box (`:60-61`) — and so is everything under
`/internal/annotation`, whose docstring (`api/routers/annotation.py:25-27`)
justifies the reads; the append-only write there is unlimited without a
sentence of its own, which is noted here for the overlay's threat model and
not filed, since the surface is unroutable from the public origin by ADR
0009.

The limiter fails open (`:154`; ADR 0005:70-73), and the ADR's own caveat is
the honest reading of what this bullet buys: *"it is not a defence against an
adversary who already has that reach"* (`:88-92`). The client key is
`sha256(request.client.host)` truncated (`:113-114`), which ADR 0005:94-100
calls obfuscation rather than anonymisation; the throttle line logs that
digest and never the address (`:157-162`).

Pinned by the fourteen tests in `test_rate_limit.py:114-288` against a stub
store, and — the part a stub cannot prove — three live steps in the `compose`
job against real Redis: thirty POSTs throttle with `Retry-After`
(`.github/workflows/ci.yml:670`), the window ends rather than extends
(`:686`), and a stopped Redis does not refuse traffic (`:704`).

**What #269 must decide is decided here — see
[the determination](#x-forwarded-for--what-269-implements).** Three pieces of
text are behind the code and belong to #269's dated addendum: ADR 0005:56-57
names three limited routes and `.env.example:82-84` names two, where the
code limits five; and `confirm-card` carries the dependency and documents
its 429 (`:953`) but no test asserts either — `test_rate_limit.py:268`
asserts two of the five wirings and `test_openapi.py:161` three.

### 7. Rate-limit image uploads — met

`POST /analyses/{id}/images` is one of the five (`api/routers/analyses.py:1149`,
the 429 documented at `:1198` and asserted present in the schema by
`test_openapi.py:161-177`). It shares the bucket with the other four on
purpose — ADR 0005:105-107: an analysis is four or five requests, and a
per-route bucket would let a client spend five windows where one was meant.

### 8. Prevent arbitrary file access — met on the public surface; `/internal` is a topology rule

On every analysis route the ownership check comes before the first byte is
read: `resolve_session` then `read_analysis`, one 404 shape for an unknown
id, another session's id, no cookie and an expired cookie
(`api/routers/analyses.py:1241-1248`; the rule at `:16-24` — *"this endpoint
must not become the one that tells a caller which analyses exist"*). No key
ever comes from a request: the upload mints one (`:1277`), a retake reads
the superseded key from the row (`:1279,1320`), and the worker reads its
inputs from rows too (`api/analysis/quality.py:285,300`,
`api/analysis/condition.py:107-108`). The readiness probe touches a
server-chosen constant, `readiness/probe` (`api/storage.py:43`).

Pinned by `test_image_upload_endpoint.py:559,576,590,602` (another session,
an unknown id, no cookie and an expired cookie are the same 404) and `:619`
(a malformed id is a 422, not a lookup).

The one route that serves stored bytes back is
`GET /internal/annotation/images/{id}/bytes`
(`api/routers/annotation.py:915-973`). Its only inputs are a UUID and a
`Literal["normalized", "original"]`; the key comes from the row
(`api/datasets/annotation.py:352-370`), never the request; the response is
`Cache-Control: private, no-store` (`:972`). It has **no authentication, no
session scoping and no rate limit**, and that is by decision, not omission:
ADR 0009:128-140 makes the annotation surface's isolation *"a separate
ingress, not routable from the public origin"*, and
`apps/annotation/README.md:48-54` says *"there is no authentication and
there should not be"*. The router is registered on the same application as
the public API (`api/app.py:144`), so today the API container's one port
serves both prefixes (`infrastructure/local/docker-compose.yml:365-380` calls
running annotation beside `web` a local convenience). **The ingress rule
that ADR 0009 relies on exists nowhere in the repository yet** —
`infrastructure/deployment/README.md` is four lines — and it is the first
line of [#273's checklist](#what-the-overlay-must-do--273s-checklist): the
proxy does not route `/internal`. The verdict is *met* because the bullet
is about arbitrary access on the surface users reach, and that surface has
none; the internal surface's control is a deployment property and is
recorded as such.

### 9. Use signed object-storage URLs — decided unnecessary in V1

The capability exists and nothing uses it. The `ObjectStorage` port carries
`signed_upload_url` and `signed_download_url` (`shared/storage/port.py:85-108`,
the latter citing §55), the S3 adapter implements both over
`generate_presigned_url` (`shared/storage/s3.py:125-165`), the in-memory
double mirrors them (`memory.py:63-90`), and `Settings` carries a TTL
(`storage_signed_url_ttl_seconds`, 900 s, `api/config.py:388-393`). A grep
over `services/`, `apps/` and `ml/` finds **no production caller** of
either.

That is a sign-off, not a gap, for a reason already written down once:
[`api.md`](api.md) at `:368-378` records the annotation bytes route's
choice to stream rather than sign — a presigned URL names the host the
*service* reaches the store on, which inside Compose is `minio:9000` and
resolves for nobody with a browser; and a signed URL is a bearer credential
nobody can revoke, a poor thing to hand out for a training photograph when
ADR 0008 makes withdrawal a right the corpus must honour. The public product
never serves an upload to anyone: `apps/web` displays no photograph, and the
only readers of stored bytes are the worker (over the SDK, with
credentials) and that internal route. A signed URL is the right control
when a browser must fetch an object directly from the store, and no browser
does.

**Re-entry:** the day an uploaded photograph is displayed to a user — a
defect overlay, a "your photographs" screen, spec §69's guided photography —
this bullet is re-read and the port's methods get their first caller. The
implementation is one call; what would need deciding is the TTL and the
revocation story.

### 10. Isolate ML workloads — not met → #273

Half of this is met and it is the half the code owns. The worker is its own
image: `worker.Dockerfile` is `api.Dockerfile` plus `--extra worker`
(`:15-17,51`), which is what brings OpenCV, and the split's reason is
written at `:12-26` — the container that decodes untrusted photographs is
not the one that answers HTTP. The pipeline is imported *inside*
`jobs._advance` (`api/analysis/jobs.py:275-289`), so the API process never
loads it; `tests/test_import_purity.py` proves no `tcg_ml_*` module is
reachable from `tcg_api.main`, and the `compose` job proves the API
*image* does not contain the distributions (`.github/workflows/ci.yml:325-361`,
importing all thirteen names in the worker and asserting each is absent from
the API container). The worker publishes no port (`worker.Dockerfile:77-78`;
`tests/test_compose_stack.py:231-233`) and drops every capability
(`docker-compose.yml:299-300`).

The other half — that the isolated workload can reach only what it needs
and write only where it must — is §56's second and third bullets, and both
are *not met*. The verdict follows them: a process that is separate but sits
on a bridge with a route to the internet and a writable root is
compartmentalised, not isolated. One further fact for the overlay: the
`ml/` *source tree* is copied into the API image's builder and runtime
(`api.Dockerfile:36,70`), even though nothing installs it; the CI assertion
is about importability, and a leaner API image would leave the tree out.

### 11. Do not execute uploaded files — met

Uploaded bytes never touch a filesystem. On the way in they live in a
`bytearray` (`api/routers/analyses.py:1098`), go to `validate_image` in a
thread (`:1263-1265`) and to `storage.put` (`:1285`). In the worker they come
back as `bytes` (`api/analysis/quality.py:300`,
`api/analysis/condition.py:107-108`), are handed to a thread
(`quality.py:301`, `condition.py:112-120`), and every stage decodes them
from memory: `cv2.imdecode(np.frombuffer(data, …))` at
`ml/card-detection/…/detector.py:196`, `ml/image-quality/…/gate.py:121`,
`ml/normalization/…/normalizer.py:156`, `ml/centering/…/measurer.py:168`,
`ml/corners/…/classifier.py:127`, `ml/edges/…/classifier.py:122` and
`ml/surface/…/classifier.py:151`; the one encode is to a buffer too
(`normalizer.py:198-199`).

The grep, every hit accounted for: `cv2.imread`, `cv2.imwrite`, `pickle`,
`torch.load`, `eval(`, `exec(`, `tempfile`, `mkstemp`, `os.system`,
`shutil` and a write-mode `open` — **zero** hits in any `ml/*/src`. The only
`subprocess` under `ml/` is in four test files that invoke their own CLIs.
In `services/api/src` the only `subprocess` is `datasets/evaluation.py:352-363`
— `shutil.which("git")` and a fixed argv, `# noqa: S603`, in a CLI the
Celery task never imports — and the only file writes are the catalog
snapshot, the dataset publisher and that same evaluation CLI, none on the
task path. Pickle appears three times, all in prose forbidding it
(`api/analysis/jobs.py:37,155-157`; `api/config.py:244`), and Celery is held
to JSON in all three settings (`jobs.py:158-160`).

The closest thing CI has to a test of this bullet is
`.github/workflows/ci.yml:657-663`, which posts `/bin/sh` as an upload and
asserts `invalid_image`; that is §1's test wearing §11's clothes, which is
the point — there is no code path on which an upload is anything but an
argument to a decoder.

### 12. Scan uploaded content where appropriate — decided unnecessary

Nothing in the repository mentions antivirus, ClamAV or malware, and until
this document nothing said whether that was a decision. It is one now, and
the reason is that **the decode is the scan.** What the bytes go through
before they are stored:

1. the streaming byte cap (§2);
2. a full Pillow decode with `LOAD_TRUNCATED_IMAGES` left off
   (`image_validation.py:22-26`), so a truncated file is refused rather than
   padded — this is the proof that the container is an image and not a
   polyglot that merely begins like one;
3. the pixel-product gate before `load()` (§2);
4. a rebuild from the image's own marker segments (JPEG, `:199-256`) or a
   re-save (PNG, `_repack_png` at `:259-275`), which is where EXIF, GPS,
   XMP, IPTC and comments go;
5. a sha256 of what was stored (`:168`), never of what arrived.

And then in the worker — a second image, uid 1001, no capabilities — OpenCV
decodes the stored bytes again and writes a fresh PNG artifact under its own
prefix (`api/analysis/quality.py:80,355-357`). Nothing serves either object
to a browser (§9). A signature scanner run over a photograph detects nothing
the two decoders do not already refuse, and it brings a parser of its own —
a larger and less exercised one than Pillow's — into the path of untrusted
bytes. That is a worse trade, not a missing control.

**What this decision does not cover, and is on the record beside it:** the
two ceilings in §2. Bytes after EOI survive the JPEG rebuild, so a JPEG that
is also something else after its last marker is stored as-is; and a
pathological aspect ratio passes the pixel gate. Neither is a route to
execution — nothing executes, serves or re-parses the stored bytes outside a
decoder — but both are why *"where appropriate"* is answered *not here* and
not *never*.

**Re-entry:** the day an uploaded object is served to a browser, or the day
an upload type that is not a raster image is accepted (a PDF, an archive, a
video frame). Either one puts a second parser in front of the bytes, and
the question is asked again from this section.

## §56 — the ML container

The four bullets are each read against `worker` in
[`docker-compose.yml`](../infrastructure/local/docker-compose.yml), which is
the only file in the repository that runs the container. That file is the
**local development stack** (ADR 0003), and two of its choices are
deliberate for that reason and are not findings against it: hot reload is a
Compose file sync into `/app` (`:328-333`; ADR 0003:63-69), which is why the
worker is not `read_only` (`:266-270` says so); and PostgreSQL, MinIO and
Redis publish host ports so the host-based test suites can reach them. The
verdicts are against the tree as it can be run today. The overlay #273
writes is where each *not met* is closed, and
[its checklist](#what-the-overlay-must-do--273s-checklist) is below.

### Run with minimal privileges — met; the overlay must keep it

The image creates `tcg` (uid/gid 1001) and drops to it
(`worker.Dockerfile:64-65,75`), in a two-stage build whose runtime stage
installs nothing with `apt` (no compiler; OpenCV arrives as a wheel).
Compose applies `security_opt: no-new-privileges:true` to every service
through one anchor (`docker-compose.yml:60-62`, at `:271` for the worker)
and gives the worker — alone — `cap_drop: [ALL]` (`:299-300`). All three are
asserted: `tests/test_compose_stack.py:216-218` (no-new-privileges on all
eight services), `:221-228` (the worker's `cap_drop`), `:255-265` (`USER
tcg` in every built Dockerfile). ADR 0003:106-111 is honest about the edge:
the claim covers the images this repository builds, and MinIO's image runs
as root.

What is missing is not a privilege but a ceiling. There is no `pids_limit`,
no `mem_limit` and no `deploy.resources` on any service, and Celery has no
`task_time_limit`, `task_soft_time_limit` or `worker_max_memory_per_child`
(a grep of `jobs.py` and `config.py` finds none). A photograph that sends a
decoder into a pathological allocation has no wall-clock, memory or process
ceiling to meet. The time limits are #272's, set from
[`observability.md`](observability.md)'s budget; the container limits are
the overlay's. Base images are pinned by tag, not digest
(`worker.Dockerfile:32,34,59`; `api.Dockerfile:21,23,58`) — #284's.

### No unnecessary network access — not met → #273

There is **no `networks:` block anywhere in the file**; its top-level keys
are `name`, `services` and `volumes` (`:27,64,396`). Every service therefore
sits on the default bridge with a route to the host and to the internet,
and every service can reach every other — the worker can reach `web:3000`
and `annotation:3001`, and they it. Six of the eight services publish a host
port (`:78-79,117-121,154-157,228-229,348-349,379-380`); only `worker` and
`migrate` do not.

What the worker actually needs is three names on three ports: `postgres:5432`,
`redis:6379`, `minio:9000` (`:47,55,310`). It needs no internet at all: no
`requests`, `httpx`, `aiohttp`, `urllib.request` or `socket` import exists
under `ml/`; the only HTTP client in the service is the catalog importer
(`api/catalog/tcgdex.py:48`), a CLI CI runs against the `api` container
(`.github/workflows/ci.yml:768`); and no model weights are fetched at build
or at run — the analyzers are heuristics whose constants are source. The gap
is entirely one of unapplied controls, which is what makes it the overlay's
to close and not the code's.

### Restricted filesystem access — not met → #273; deliberate locally

No service sets `read_only`, `tmpfs` or `user:`. For the worker the omission
is written down (`:266-270`): the file sync would break, and *"a deployment
that does not hot-reload should take the filesystem"*. The worker's one
write of its own is Celery beat's schedule at `/tmp/celerybeat-schedule`
(`:281-292`), which goes to the container's writable layer because nothing
mounts a tmpfs there. On the application side the verdict is clean —
nothing on the task path writes a file (§11) — so `read_only: true` with a
tmpfs for `/tmp` costs the overlay nothing but the schedule's home.

### Never execute files from upload directories — met

There is no upload directory. Uploads are objects; they reach the worker as
`bytes` and reach the decoders as a NumPy view of those bytes (§11 has every
site). Nothing writes them, nothing lists them, nothing executes anything.
The one `subprocess.run` in the worker image is `git`, fixed argv, in a CLI
the task never imports (`api/datasets/evaluation.py:352-363`).

## The two determinations the issue asked for

### `X-Forwarded-For` — what #269 implements

ADR 0005:67-68 refused the header because *"a header any client can set is
a bypass rather than an identity, and trusting one requires knowing which
proxy is in front of this service"*. That was the right refusal with no
proxy and is the wrong one behind #273's, where `request.client.host` is
the proxy's address and every beta user shares one bucket of thirty
requests a minute. The determination, which #269 implements and ADR 0005's
dated addendum records:

- **`Settings.trusted_proxy_count: int = 0`** (`TCG_API_TRUSTED_PROXY_COUNT`).
  At `0` — the default, and what CI runs — behaviour is exactly ADR 0005's:
  the header is never read.
- **At *n*, the client is the address *n* hops from the right** of
  `X-Forwarded-For` — the one the *n*-th trusted proxy appended. Never the
  first entry: the leftmost values are the client's to write. A request
  carrying fewer than *n* hops keys on `request.client.host`, as now.
- **The overlay's proxy overwrites the header** rather than appending to a
  client-supplied one; #273 says so in the proxy's configuration, and #269
  tests the parser on a header that carries junk before the real hop.
- One header, one count. No `Forwarded` (RFC 7239), no `X-Real-IP`; no
  change to the window, the limit, the fail-open rule or the 429's shape.

`client_key` in `rate_limit.py` stays the one place this is decided; the
hashing and the prefix do not change.

### Content scanning — decided unnecessary

Recorded in [§12](#12-scan-uploaded-content-where-appropriate--decided-unnecessary)
with its reason and its two re-entry conditions, so that the next reader
finds the decision where the bullet is.

## What the overlay must do — #273's checklist

Each line is a finding above, with the line it was read from. The overlay
closes them; the local file keeps its deliberate choices.

1. **Do not route `/internal`.** The proxy in front of `api` forwards
   `/analyses`, `/cards`, `/grading-companies`, `/catalog`, `/health`,
   `/readiness` and nothing under `/internal/` (ADR 0009:131-138;
   `api/routers/annotation.py:11-13`). `annotation` runs on the internal
   network only.
2. **An internal network, `internal: true`,** shared by `worker`,
   `postgres`, `minio`, `redis` and `api`, with no egress; `web` and the
   proxy on a second network that reaches `api` alone
   (`docker-compose.yml` has no `networks:` at all).
3. **No published datastore ports** — `postgres`, `minio`, `redis` and the
   MinIO console stay off the host (`:78-79,117-121,154-157`).
4. **`read_only: true` on `worker`, with `tmpfs: /tmp`**, and beat's
   schedule at `/tmp/celerybeat-schedule` (`:281-292`) or on a named
   volume; the same on `api` if its stage writes nothing.
5. **`mem_limit` and `pids_limit` on `worker`**, sized from
   [`observability.md`](observability.md)'s figures once a real photograph
   has been timed; #272's `task_time_limit` is the wall-clock half.
6. **Keep** `cap_drop: ALL` (`:299-300`), `no-new-privileges` (`:60-62`)
   and uid 1001 (`worker.Dockerfile:64-65,75`); the overlay's own
   compose-file test asserts them, `test_compose_stack.py`'s
   `EXPECTED_SERVICES` stays the local file's.
7. **A scoped MinIO service account** for `api` and `worker` with access to
   the one bucket, in place of the root credentials both hold today
   (`:211-214,310-313` are `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from
   `:114-116`; `.env.example:199-200` documents the equality as intended).
8. **`rediss://` with real credentials** for the broker, as
   `api/config.py:242-245` and `.env.example:71-76` already require of a
   deployment; today nothing validates the scheme.
9. **The proxy overwrites `X-Forwarded-For`** and the overlay sets
   `TCG_API_TRUSTED_PROXY_COUNT=1` (the determination above).
10. **`TCG_API_LOG_FORMAT` unset**, so JSON stands (#266).

## What stays open, and where

| Finding | Where it lands |
| --- | --- |
| No dependency audit, no image scan, tag-pinned base images, dependency review PR-only | **#284** |
| No `SECURITY.md`, no private disclosure path | **#285** |
| No egress restriction, writable worker root, no container limits, root MinIO credentials, published datastore ports, `/internal` reachable on the API's port | **#273** (checklist above) |
| No Celery time limit | **#272** |
| Limiter keys on the socket behind a proxy | **#269** (determination above) |
| ADR 0005 and `.env.example` name three and two limited routes; `confirm-card` has no 429 test | **#269**'s addendum |
| `worker.py:12-15` still says the worker runs from the API's image (stale since #36) | **#272**, which touches the module |
| JPEG trailer / progressive hole; no per-axis cap | On the record in §2 and §12; re-entry conditions named, nothing filed |
| The `ml/` source tree is in the API image | Noted for #268 / #273; not a finding against any bullet |
| The unsalted address digest in the throttle log | ADR 0005:94-100's own caveat; the TTL bounds it; nothing filed |

What this document does **not** decide: whether `/internal` ever gets
authentication (ADR 0009:139-140 names the two triggers), the platform the
overlay runs on (spec §75, a later ADR), and any threshold in
`image_validation.py` — a change there is a version bump with a measured
reason, not a review finding.

## Issues filed from this review

| Issue | Closes |
| --- | --- |
| [#284](https://github.com/chuanseng-ng/tcg-analyzer/issues/284) `ci: audit dependencies and scan the built images` | the audit gap noted under §56.1 and in Method |
| [#285](https://github.com/chuanseng-ng/tcg-analyzer/issues/285) `docs: add SECURITY.md` | the missing disclosure path |
| [#273](https://github.com/chuanseng-ng/tcg-analyzer/issues/273) (existing) | §55.10, §56.2, §56.3 — the checklist above, posted to the issue |
| [#269](https://github.com/chuanseng-ng/tcg-analyzer/issues/269) (existing) | the `X-Forwarded-For` determination and the three text fixes |
