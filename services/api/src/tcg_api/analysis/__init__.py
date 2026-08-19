"""The analysis pipeline's PostgreSQL side.

`tcg_domain.analysis` says what an analysis may *be* — the four closed
vocabularies of spec §65, §19, §11 and §52. This package says how the rows are
shaped. The split is the same one the catalog makes, and for the same reason:
the domain package imports nothing but the standard library, so it can be
imported by every ML module without dragging a database driver along.

`sessions.py` starts and reads an analysis, `state.py` moves it, and `jobs.py`
is the queue a move actually happens on — `worker.py` is what a worker process
is pointed at. The upload endpoint and every pipeline stage are separate issues;
what exists at this point is a record, and a harness that can carry it from one
state to the next.

The package deliberately exports only the tables. Importing `jobs` pulls in
Celery, and the only thing that should pay for that is code that enqueues.
"""

from __future__ import annotations

from tcg_api.analysis.tables import TABLES, analyses, analysis_sessions, images

__all__ = ["TABLES", "analyses", "analysis_sessions", "images"]
