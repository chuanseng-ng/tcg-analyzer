"""Writing and reading one economic configuration.

`tables.py` says what an `economic_configurations` row *is*; this module is the
only place one is written or read. The split matches the catalog's, the grading
rules' and the market's: DDL there, statements here.

**Writing a configuration and linking it are one operation.** A row nothing
points at is a row nobody can find — the table has no other index and no listing
query — so `create_configuration` does both and returns `None` if the analysis
already has one. The link is a conditional `UPDATE` (`WHERE
economic_configuration_id IS NULL`), which is `tcg_api.analysis.state`'s
argument reused: a read, a check and then a write would be three statements with
a race between them, and two submissions arriving together is exactly the race a
mobile client's double-tap produces. `rowcount == 1` means "this caller is the
one that set it".

**Recording a configuration is what completes the analysis** (#244, spec §65).
After #228 nothing asynchronous remains between a configuration and its results
— the predictions were stored at the worker's claim and the results are composed
on read — so the configuration is the last input the results need, and the same
transaction that links it moves the analysis `analyzing → calculating →
completed` through `tcg_api.analysis.state.transition`, twice. `calculating` is
passed through and never held, exactly as `queued` is a transport word no row
ever carries; §65 lists nine states and the chain is the contract, so there is no
`analyzing → completed` edge to take instead. `completed` means "every input the
results need is recorded", not "a results row exists".

**This is not `record_reproducibility`, deliberately.** That function runs
inside the worker's claim and writes the versions in force *when the analysis
ran*; a configuration is user input that does not exist at claim time, so a
parameter there would be one every caller passes `None` — which is how a caller
that ought to record something silently stops. `record_reproducibility`'s own
docstring makes the same argument for `grading_rules_version`. What keeps the
record immutable is unchanged either way:
`trg_analyses_reproducibility_immutable` allows NULL → value once and refuses a
second write, whichever statement performs it.

**Rows become the engine's own types.** Nothing here returns a mapping or a
`sa.Row`. `CostConfiguration`, `SellingFee`, `Money` and
`RecommendationThresholds` are constructed on the way out, so a stored value
that no longer satisfies the engine's own validation is caught when it is read
as well as when it is written — and no caller downstream re-parses a `Decimal`
that has already been validated once.

**The thresholds are written here, from `DEFAULT_THRESHOLDS`, and are not a
parameter.** They are policy rather than a card's costs, and #64 binds that
they are #65's to persist: storing them is what makes a recommendation
reproducible when M7/M8's calibration moves them. A parameter would be the seam
through which a client eventually sets its own gate on its own recommendation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import AnalysisStatus
from tcg_domain.confidence import Confidence
from tcg_domain.money import Currency, Money
from tcg_economic_engine import (
    DEFAULT_THRESHOLDS,
    CostConfiguration,
    RecommendationThresholds,
    SellingFee,
)

from tcg_api.analysis.state import transition
from tcg_api.analysis.tables import analyses
from tcg_api.database import execute as _execute
from tcg_api.economics.tables import economic_configurations

__all__ = [
    "EconomicConfiguration",
    "EconomicConfigurationUnavailable",
    "create_configuration",
    "read_configuration",
]

_UNAVAILABLE: Final = "The economic configuration store could not be reached."


class EconomicConfigurationUnavailable(ConnectionError):
    """The economic configuration store could not be read or written.

    A `ConnectionError` for the reason `CatalogUnavailable`,
    `AnalysisStoreUnavailable` and `MarketSnapshotUnavailable` are: the store
    being unreachable is a transport fact, and a caller that only cares about
    that can catch the builtin.

    Its own type rather than `AnalysisStoreUnavailable`, even though both live
    in the same database, because a route turns it into a `details.reason` — and
    an operator reading a 503 should be told which store the statement was
    against rather than have to guess from the path.
    """


@dataclass(frozen=True, slots=True)
class EconomicConfiguration:
    """One stored configuration, as the engine's own types.

    Frozen because the row is: the database refuses an `UPDATE` and this refuses
    an assignment, so the two agree about what a configuration is.
    """

    id: UUID
    created_at: datetime
    #: Spec §46's line items. **Not a total** — #58 binds that there is none.
    costs: CostConfiguration
    #: Spec §45's optional input. `None` is "they did not say"; `Money.zero()` is
    #: a real acquisition cost, and the two reach different §41 answers.
    acquisition_cost: Money | None
    #: The companies to compare — slugs, in the order they were given.
    companies: tuple[str, ...]
    #: Spec §43's mode, as a `str`: #63 binds that a sixth needs no code change.
    optimization_mode: str
    #: #64's five gates, as they stood when this configuration was written.
    thresholds: RecommendationThresholds


_COLUMNS: Final = (
    economic_configurations.c.id,
    economic_configurations.c.created_at,
    economic_configurations.c.currency,
    economic_configurations.c.acquisition_cost,
    economic_configurations.c.grading_fee,
    economic_configurations.c.outbound_shipping,
    economic_configurations.c.return_shipping,
    economic_configurations.c.insurance,
    economic_configurations.c.miscellaneous,
    economic_configurations.c.selling_fee_rate,
    economic_configurations.c.selling_fee_flat,
    economic_configurations.c.grading_companies,
    economic_configurations.c.optimization_mode,
    economic_configurations.c.minimum_image_quality,
    economic_configurations.c.minimum_grade_confidence,
    economic_configurations.c.minimum_figure_confidence,
    economic_configurations.c.maximum_unpriced_probability,
    economic_configurations.c.minimum_incremental_profit,
)


async def execute(db: AsyncSession, statement: sa.Executable) -> sa.Result[Any]:
    """Run `statement`, translating every driver failure into one exception."""
    return await _execute(
        db,
        statement,
        unavailable=EconomicConfigurationUnavailable,
        message=_UNAVAILABLE,
    )


def _entity(row: sa.Row[Any]) -> EconomicConfiguration:
    """Rebuild the engine's types from one row.

    Every amount goes back through `Money`, and the fee back through
    `SellingFee`, so the validation that refused a percentage-shaped rate on the
    way in refuses it on the way out too. `Numeric` comes back from asyncpg as a
    `Decimal`, which is what `Money` requires and what a `float` column could
    never have given it.
    """
    currency = Currency(row.currency)

    def amount(value: Decimal) -> Money:
        return Money(value, currency)

    return EconomicConfiguration(
        id=row.id,
        created_at=row.created_at,
        costs=CostConfiguration(
            grading_fee=amount(row.grading_fee),
            outbound_shipping=amount(row.outbound_shipping),
            return_shipping=amount(row.return_shipping),
            insurance=amount(row.insurance),
            miscellaneous=amount(row.miscellaneous),
            selling_fee=SellingFee(rate=row.selling_fee_rate, flat=amount(row.selling_fee_flat)),
        ),
        acquisition_cost=None if row.acquisition_cost is None else amount(row.acquisition_cost),
        companies=tuple(row.grading_companies),
        optimization_mode=row.optimization_mode,
        thresholds=RecommendationThresholds(
            minimum_image_quality=Confidence(row.minimum_image_quality),
            minimum_grade_confidence=Confidence(row.minimum_grade_confidence),
            minimum_figure_confidence=Confidence(row.minimum_figure_confidence),
            maximum_unpriced_probability=row.maximum_unpriced_probability,
            minimum_incremental_profit=amount(row.minimum_incremental_profit),
        ),
    )


async def create_configuration(
    db: AsyncSession,
    analysis_id: UUID,
    *,
    costs: CostConfiguration,
    acquisition_cost: Money | None,
    companies: Sequence[str],
    optimization_mode: str,
) -> EconomicConfiguration | None:
    """Store a configuration, attach it to `analysis_id`, and complete the analysis.

    Returns the stored configuration, or `None` if nothing was recorded: the
    analysis already has one — which the caller answers with a 409, because a
    configuration is immutable and re-running with different costs is a new
    analysis — or it is not `analyzing`, so the link won and the move could not.
    The caller's state check makes the second case a race rather than a path,
    and the rollback answers both the same way.

    The INSERT comes first: the foreign key is checked when the analysis row is
    updated, so the configuration has to exist by then. A caller that gets `None`
    back must roll the transaction back, which drops the row the link never
    reached; this function does not commit, so that is one statement in the
    caller rather than a compensating delete here. The two transitions ride in
    the same transaction, so an analysis is never `completed` without its
    configuration and never linked without being `completed`.

    Thresholds are written from :data:`DEFAULT_THRESHOLDS` and are deliberately
    not a parameter — see the module docstring.

    Raises:
        EconomicConfigurationUnavailable: If the configuration store could not
            be reached.
        AnalysisStoreUnavailable: If the analysis row could not be moved —
            `transition` speaks for the analysis store, not this one.
    """
    identifier = uuid4()
    currency = costs.grading_fee.currency
    thresholds = DEFAULT_THRESHOLDS
    insert = (
        sa.insert(economic_configurations)
        .values(
            id=identifier,
            currency=currency.value,
            acquisition_cost=None if acquisition_cost is None else acquisition_cost.amount,
            grading_fee=costs.grading_fee.amount,
            outbound_shipping=costs.outbound_shipping.amount,
            return_shipping=costs.return_shipping.amount,
            insurance=costs.insurance.amount,
            miscellaneous=costs.miscellaneous.amount,
            selling_fee_rate=costs.selling_fee.rate,
            selling_fee_flat=costs.selling_fee.flat.amount,
            grading_companies=list(companies),
            optimization_mode=optimization_mode,
            minimum_image_quality=thresholds.minimum_image_quality.value,
            minimum_grade_confidence=thresholds.minimum_grade_confidence.value,
            minimum_figure_confidence=thresholds.minimum_figure_confidence.value,
            maximum_unpriced_probability=thresholds.maximum_unpriced_probability,
            minimum_incremental_profit=thresholds.minimum_incremental_profit.amount,
        )
        .returning(*_COLUMNS)
    )
    stored = _entity((await execute(db, insert)).one())

    link = (
        sa.update(analyses)
        .where(
            analyses.c.id == analysis_id,
            analyses.c.economic_configuration_id.is_(None),
        )
        .values(economic_configuration_id=identifier)
    )
    # `execute` is typed for the reads it was written for; an UPDATE always
    # produces a `CursorResult`, which is the only kind that counts rows. The
    # cast target is unquoted deliberately, for `analysis/state.py`'s reason.
    result = cast(sa.CursorResult[Any], await execute(db, link))
    if result.rowcount != 1:
        return None

    # Two moves, not one: `TRANSITIONS` is a linear chain and `transition`
    # enforces the legal predecessor. Either refusing means the analysis was not
    # `analyzing`, and the caller's rollback drops the row and the link together.
    completed = await transition(
        db, analysis_id, to=AnalysisStatus.CALCULATING
    ) and await transition(db, analysis_id, to=AnalysisStatus.COMPLETED)
    return stored if completed else None


async def read_configuration(
    db: AsyncSession, configuration_id: UUID
) -> EconomicConfiguration | None:
    """One configuration by its identifier, or `None` if there is no such row.

    This is spec §57's record read back: an analysis holds the identifier in
    `analyses.economic_configuration_id` and resolves the exact numbers here,
    however many configurations have been written since.

    Raises:
        EconomicConfigurationUnavailable: If the store could not be reached.
    """
    statement = sa.select(*_COLUMNS).where(economic_configurations.c.id == configuration_id)
    row = (await execute(db, statement)).one_or_none()
    return None if row is None else _entity(row)
