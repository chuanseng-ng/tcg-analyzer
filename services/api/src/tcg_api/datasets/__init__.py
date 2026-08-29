"""Dataset, provenance and membership storage — spec §29's fields and §31 and §32's versions.

ADR 0009 decided that this store is a **schema domain in PostgreSQL** and not
the `datasets/` directory: `datasets/schemas/` describes these shapes for a
human, `datasets/manifests/` holds a manifest generated *from* these rows, and
the migration is the only DDL. ADR 0008 decided what may enter it — four
approved sources, and a gate where an unknown answer is a refusal.

`tables.py` declares §32's `physical_copies`, §29's `training_images`, the
`training_image_fingerprints` §32's splitter groups on, §31's `dataset_versions`
with their `dataset_members`, and §30's `image_annotations` and
`centering_measurements` — the labels every model M7 and M8 train learns from. The gate is a `CHECK` on
`training_images` rather than a function a loader remembers to call, which is
ADR 0009's whole argument: an image nobody has the right to train on is not
representable in this schema.

**Nothing here is on the public §64 API.** The consumer product never reads a
training image. Spec §30's annotation tool does, at `/internal/annotation` in
this same application — in this schema because ADR 0001 makes the OpenAPI
document the only way `apps/annotation` can learn a shape, and isolated by
deployment topology rather than by a second FastAPI application: the
`/internal` prefix is what an ingress matches.

`normalization.py` is the one module here that binds to OpenCV, on
`deduplication.py`'s terms. It stores the standardized artifact the annotation
tool shows and §30's coordinates are fractions of — out of band, because a
request path that could straighten a photograph would be a request path with the
CV stack in it.
"""

from __future__ import annotations
