"""The model registry — spec §58's store for §59's versioned bundles.

The seventh schema domain. One table, `model_bundles`, whose job is to let an
analysis record `condition-model-v0.3.0` and mean one immutable artifact
forever: the registry stores the reference — a name, a version, the dataset it
trained on, its configuration and metrics, and an object-storage key — and the
store holds the bytes. Model weights enter neither git nor the database.

Two boundaries hold the domain small. **Heuristic analyzer versions are code
constants, never registry rows** (epic #8's decomposition decision 5): a row
here names a *trained* artifact with a dataset version and metrics, and
null-filling those fields for `centering-opencv-v0.1.0` would make the
registry lie — so the table lands empty and its first row is written when the
first trained bundle exists. And the registry is **operational, not §64's
surface**: registration is a library and a console script, and there is no
HTTP route, no serving automation and no download endpoint.
"""

from __future__ import annotations
