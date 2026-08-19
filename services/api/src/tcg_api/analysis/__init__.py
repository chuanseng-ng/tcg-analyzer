"""The analysis pipeline's PostgreSQL side.

`tcg_domain.analysis` says what an analysis may *be* — the four closed
vocabularies of spec §65, §19, §11 and §52. This package says how the rows are
shaped. The split is the same one the catalog makes, and for the same reason:
the domain package imports nothing but the standard library, so it can be
imported by every ML module without dragging a database driver along.

Nothing here runs an analysis. The state machine, the job queue and the upload
endpoint are separate issues; what exists at this point is the record they will
write to.
"""

from __future__ import annotations

from tcg_api.analysis.tables import TABLES, analyses, analysis_sessions, images

__all__ = ["TABLES", "analyses", "analysis_sessions", "images"]
