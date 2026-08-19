"""Every table this service declares, assembled in one place.

`tcg_api.tables` owns the `MetaData`; the domain modules own the tables and
attach them to it. A `sa.Table` attaches when its module is *executed*, so a
`MetaData` handed to Alembic before those modules have been imported is empty —
and `alembic revision --autogenerate` proposes dropping every table it cannot
see. Importing this module is what executes them, which is why
`database/migrations/env.py` reads `metadata` from here rather than from
`tcg_api.tables`.

The dependency runs one way only — this module imports the domain modules, and
they import `tcg_api.tables` — so there is no cycle and no import kept alive
purely for its side effect.

**A new domain is added here**, and every caller that wants the whole schema
keeps working unchanged.
"""

from __future__ import annotations

from typing import Final

from tcg_api.analysis.tables import TABLES as ANALYSIS_TABLES
from tcg_api.catalog.tables import TABLES as CATALOG_TABLES
from tcg_api.tables import metadata

__all__ = ["DECLARED_TABLES", "metadata"]

#: Every declared table, catalog first — which is also a workable creation
#: order, since `analyses.card_id` references `cards`.
DECLARED_TABLES: Final = (*CATALOG_TABLES, *ANALYSIS_TABLES)
