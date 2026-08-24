"""Resolving which published grading standard was in force, and when.

`tables.py` says what a `grading_rules` row *is*; this module is the only place
that reads one. The split matches the catalog's: DDL there, statements here.

**Rows become entities.** Nothing here returns a mapping. Constructing
:class:`tcg_grading_companies.GradingRules` is the validation on the way out —
the same trick `tcg_api.catalog.versions` plays.

**Plain functions, no Protocol.** `CardRepository` and `ObjectStorage` are ports
because a catalog source and an object store are replaceable external providers;
published grading rules are reference data this repository publishes into its own
database, so a port here would be an interface with one implementation. The
precedent is `tcg_api.analysis.sessions`, which is module functions for the same
reason.

Two things about `effective_to` are worth reading before the queries:

* **It is computed, never stored.** A version is in force from its
  `effective_from` until the next version of the same company begins, so
  `lead()` over the company's rows *is* the range. `tables.py` gives the full
  argument; the consequence here is that every record returned carries an
  `effective_to`, so a caller cannot tell the difference.
* **The range is half-open — `[effective_from, next_effective_from)`.** A
  version is in force *on* its own effective date and *not* on its successor's.
  That is what a company's own wording says: "Starting February 1, 2008, all
  cards submitted to PSA will be graded utilizing this new scale" makes
  2008-02-01 the new scale's first day, not the old one's last.

A NULL `effective_from` sorts first and matches every date. The company states
no start, so as far as this repository knows the standard applied until something
succeeded it — and it comes back as `None` rather than as a date nobody
published.

`GradingRulesUnavailable` exists for the reason `analysis/sessions.py`'s
`AnalysisStoreUnavailable` does: #48 put a route in front of these functions, and
a driver error on an HTTP path would otherwise reach a client as a 500 carrying
an asyncpg message. It is defined here rather than in `tcg_domain.errors`,
where `CatalogUnavailable` lives, because nothing here implements a domain
Protocol — published grading rules are this repository's own reference data.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_grading_companies import GradingRules

from tcg_api.grading.tables import grading_rules

__all__ = ["GradingRulesUnavailable", "get_rules", "rules_in_force"]

_UNAVAILABLE = "The grading rules could not be read."


class GradingRulesUnavailable(ConnectionError):
    """The grading rules store could not be reached.

    A `ConnectionError`, matching `AnalysisStoreUnavailable` and the domain's
    `CatalogUnavailable`: what went wrong is that the store was out of reach,
    not that the caller asked for something invalid.
    """


#: Each row with the start of the row that supersedes it, which is the end of its
#: own range. `NULLS FIRST` puts an undated standard before every dated one, so a
#: company that later publishes an effective date supersedes its own undated
#: record rather than sitting beside it.
_EFFECTIVE_TO: Final = (
    sa.func.lead(grading_rules.c.effective_from)
    .over(
        partition_by=grading_rules.c.company,
        order_by=grading_rules.c.effective_from.asc().nulls_first(),
    )
    .label("effective_to")
)

#: A window function cannot appear in a WHERE clause — it is evaluated after
#: filtering — so the ranges are built in a subquery and both readers filter the
#: result. One statement either way.
_RANGED: Final = sa.select(
    grading_rules.c.company,
    grading_rules.c.version,
    grading_rules.c.effective_from,
    grading_rules.c.source,
    grading_rules.c.verified_on,
    grading_rules.c.rules,
    _EFFECTIVE_TO,
).subquery("ranged")

_SELECT: Final = sa.select(_RANGED)


async def _one_or_none(db: AsyncSession, statement: sa.Select[Any]) -> sa.Row[Any] | None:
    """Run a statement that resolves at most one version, translating failure.

    `OSError` alongside `SQLAlchemyError` because a refused connection never
    becomes a SQLAlchemy error at all — the same rule `catalog/cards.py`,
    `catalog/versions.py` and `analysis/sessions.py` each state for themselves.

    `one_or_none()` rather than `first()`: both readers rely on at most one row
    matching, and a second one means the non-overlap invariant `tables.py`
    argues for has been broken. Raising there is the honest outcome.

    ponytail: fourth near-identical wrapper in this service. Hoist
    `catalog/cards.py::_execute`, `catalog/versions.py::_one_or_none`,
    `analysis/sessions.py::execute` and this one into one helper parameterised
    on the exception if a fifth ever appears.
    """
    try:
        result = await db.execute(statement)
    except (SQLAlchemyError, OSError) as error:
        raise GradingRulesUnavailable(_UNAVAILABLE) from error
    return result.one_or_none()


def _entity(row: sa.Row[Any]) -> GradingRules:
    return GradingRules(
        company=row.company,
        version=row.version,
        effective_from=row.effective_from,
        source=row.source,
        verified_on=row.verified_on,
        effective_to=row.effective_to,
        rules=row.rules,
    )


async def rules_in_force(db: AsyncSession, company: str, on: date) -> GradingRules | None:
    """The standard `company` was grading to on `on`, or `None` if there is none.

    `None` means either that no version of this company is recorded at all, or
    that `on` precedes the earliest effective date one states. Both are honest
    answers; neither falls back to the oldest version, which would report a
    standard as in force before it existed.

    At most one row can match. The intervals partition each company's timeline
    by construction, so there is no `LIMIT 1` here to get the ordering of wrong.

    Raises:
        GradingRulesUnavailable: If the store cannot be reached.
    """
    statement = _SELECT.where(
        _RANGED.c.company == company,
        sa.or_(_RANGED.c.effective_from.is_(None), _RANGED.c.effective_from <= on),
        sa.or_(_RANGED.c.effective_to.is_(None), _RANGED.c.effective_to > on),
    )
    row = await _one_or_none(db, statement)
    return None if row is None else _entity(row)


async def get_rules(db: AsyncSession, version: str) -> GradingRules | None:
    """One published version by its identifier, or `None` if it is not recorded.

    This is spec §23's "historical analyses must retain the rules version used"
    read back: an analysis holds the identifier in
    `analyses.grading_rules_version` and resolves the exact record here, however
    many revisions have been published since. Reads through the same ranged
    subquery, so a superseded version still reports the date it stopped applying.

    Raises:
        GradingRulesUnavailable: If the store cannot be reached.
    """
    statement = _SELECT.where(_RANGED.c.version == version)
    row = await _one_or_none(db, statement)
    return None if row is None else _entity(row)
