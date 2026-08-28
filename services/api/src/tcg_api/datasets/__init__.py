"""Dataset, provenance and membership storage — spec §29's fields and §31 and §32's versions.

ADR 0009 decided that this store is a **schema domain in PostgreSQL** and not
the `datasets/` directory: `datasets/schemas/` describes these shapes for a
human, `datasets/manifests/` holds a manifest generated *from* these rows, and
the migration is the only DDL. ADR 0008 decided what may enter it — four
approved sources, and a gate where an unknown answer is a refusal.

`tables.py` declares §32's `physical_copies`, §29's `training_images`, the
`training_image_fingerprints` §32's splitter groups on, and §31's
`dataset_versions` with their `dataset_members`. The gate is a `CHECK` on
`training_images` rather than a function a loader remembers to call, which is
ADR 0009's whole argument: an image nobody has the right to train on is not
representable in this schema.

**Nothing here is on the public §64 API.** The consumer product never reads a
training image, and the annotation tool's isolation is a deployment question —
a separate ingress, not a second FastAPI application.
"""

from __future__ import annotations
