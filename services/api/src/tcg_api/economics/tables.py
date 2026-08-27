"""Spec §46's cost configuration, as a table — the fifth domain in this schema.

One row is one **immutable** answer to "what would grading this card cost me,
and what am I optimizing for?": §46's six line items, §45's optional acquisition
cost, the companies to compare, the optimization mode, and #64's five
recommendation thresholds. `analyses.economic_configuration_id` points at it,
which is what makes spec §57's reproducibility record complete for the economics
half of the pipeline.

Four decisions are inherited rather than made here, and a reviewer should know
which is which:

* **Named line items, never a total.** #58 binds it: §47's future dimensions —
  country, tax, service tier, shipping provider — attach to *individual* lines,
  and a stored total makes every one of them a rewrite. There is no
  `total_costs` column and there must never be one.
* **The selling fee is a rate plus a flat part, and the rate is a proportion.**
  Ten percent is `0.10`. `Decimal("10")` is refused by `SellingFee` at the
  boundary and by `ck_economic_configurations_selling_fee_rate_is_a_proportion`
  here, so neither can be the only guard.
* **`acquisition_cost` is nullable and `0.00` is a real value.** §45 forbids
  inferring it; #61 answers an absent one with
  `acquisition_cost_not_supplied`. A NOT NULL column with a zero default would
  turn "I don't remember" into "it was free", which is the same fabrication
  #91's "Not measured" is never `0%`.
* **The thresholds are stored but never accepted from a client.** They are
  policy, written from `DEFAULT_THRESHOLDS`; storing them is what keeps a
  recommendation reproducible when M7/M8's calibration moves them. Recalibration
  then produces new configurations rather than silently reinterpreting old
  answers — the same rule every other §57 value follows.

**Two columns deliberately carry no CHECK**, where a reader may expect one.
`optimization_mode` is a `str` because §43 requires future modes and #63 binds
that a sixth needs no change to the engine; `grading_companies` names companies
because `grading_rules.company` does, so a fourth company stays one adapter and
no migration. Both are validated at the HTTP boundary against `STRATEGIES` and
`ADAPTERS` — the same source `GET /grading-companies` serves.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_domain.money import Currency

from tcg_api.tables import PRINTED, metadata, one_of

__all__ = ["TABLES", "economic_configurations"]


#: Every monetary column here. Two places, like `market_observations.price`.
_MONEY: Final = sa.Numeric(12, 2)


def _amount(name: str, comment: str, *, nullable: bool = False) -> sa.Column[Decimal]:
    """One non-negative money column, with the CHECK that makes it non-negative.

    A helper rather than seven near-identical blocks: ADR 0007 asserts that
    neither `CapitalAtRisk` denominator can be negative "because both are sums of
    non-negative quantities", and that claim is only true while every one of
    these columns says so. Written once, so a line item added later cannot be the
    one that quietly omits it.
    """
    return sa.Column(
        name,
        _MONEY,
        nullable=nullable,
        comment=comment,
    )


# PostgreSQL truncates an identifier at 63 bytes, and the naming convention
# already spends 27 of them on `ck_economic_configurations_`. SQLAlchemy renders
# an over-long name as a truncated stem plus a hash, and the reflected name then
# differs from the declared one — which `alembic revision --autogenerate` reports
# as a constraint dropped and re-added on **every** run, for ever. So the four
# thresholds and the profit margin carry short names that do not simply echo
# their column. `test_economics_tables.py` asserts the whole table stays inside
# the limit, so the next column added cannot quietly reintroduce this.
def _non_negative(
    name: str, *, nullable: bool = False, as_name: str | None = None
) -> sa.CheckConstraint:
    condition = f"{name} >= 0"
    return sa.CheckConstraint(
        f"{name} IS NULL OR {condition}" if nullable else condition,
        name=as_name or f"{name}_is_not_negative",
    )


def _unit_interval(name: str, as_name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{name} >= 0 AND {name} <= 1",
        name=as_name,
    )


economic_configurations = sa.Table(
    "economic_configurations",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "currency",
        PRINTED,
        nullable=False,
        server_default=sa.text(f"'{Currency.SGD.value}'"),
        comment=(
            "What every amount in this row is denominated in. Constrained to what "
            "`tcg_domain.money.Currency` models, deliberately unlike "
            "`market_observations.currency`, which admits any ISO 4217 code because an "
            "observation records what a provider actually said. This row records what a "
            "user was quoted in, and V1 quotes in SGD (§46); #53 owns conversion."
        ),
    ),
    _amount(
        "acquisition_cost",
        (
            "What the user paid for the card — spec §45's optional input. **NULL means "
            "they did not say, and `0.00` is a real acquisition cost**: a raffle win, a "
            "pack out of somebody else's box. The two are different answers and #61 "
            "reports the first as `acquisition_cost_not_supplied` rather than as a "
            "number. Never inferred, and never filled in from the raw market price — "
            "that answers 'what if you had bought it today?', which is a different "
            "question."
        ),
        nullable=True,
    ),
    _amount(
        "grading_fee",
        "§46's `grading_fee`: the company's charge per card for the chosen tier.",
    ),
    _amount(
        "outbound_shipping",
        "§46's `outbound_shipping`: getting the card to the grader.",
    ),
    _amount(
        "return_shipping",
        (
            "§46's `return_shipping`: getting it back. Separate from outbound because "
            "the two differ — return is insured and often faster — and §47 will make "
            "them differ more."
        ),
    ),
    _amount("insurance", "§46's `insurance`: cover for the round trip."),
    _amount(
        "miscellaneous",
        "§46's `miscellaneous`: sleeves, semi-rigids, a courier surcharge.",
    ),
    sa.Column(
        "selling_fee_rate",
        sa.Numeric(6, 4),
        nullable=False,
        comment=(
            "The proportion of the realised sale price taken as commission — **a "
            "proportion in [0, 1], never a percentage**. Ten percent is 0.1000. Four "
            "decimal places, because a marketplace quoting 12.9% is ordinary and "
            "rounding it to 0.13 is a fee the user never agreed to."
        ),
    ),
    _amount(
        "selling_fee_flat",
        (
            "The fixed part of §46's `selling_fee`, charged per sale regardless of "
            "price. Together with the rate this is one line item in two columns — and "
            "it is the one line item ADR 0007 keeps **out** of `CapitalAtRisk`, because "
            "it is paid out of proceeds rather than committed up front."
        ),
    ),
    sa.Column(
        "grading_companies",
        postgresql.ARRAY(sa.Text()),
        nullable=False,
        comment=(
            "Which companies to compare — spec §48's multi-select, and §49's "
            "'Compare PSA / TAG / BGS'. Slugs, matching `GET /grading-companies`. **No "
            "CHECK naming the three**, exactly as `grading_rules.company` carries none: "
            "a fourth company is one adapter and no migration. The HTTP boundary "
            "validates against `ADAPTERS`, which is the same source that endpoint serves."
        ),
    ),
    sa.Column(
        "optimization_mode",
        PRINTED,
        nullable=False,
        comment=(
            "Spec §43's optimization mode. **No CHECK, deliberately**: §43 says the "
            "architecture must allow future modes and #63 makes a mode a `str` that "
            "`rank` is handed, so a sixth needs no change to the engine — and a CHECK "
            "here would make it need a migration. Validated at the boundary against "
            "`STRATEGIES`."
        ),
    ),
    sa.Column(
        "minimum_image_quality",
        sa.Double(),
        nullable=False,
        comment=(
            "#64's gate on what spec §19 made of the photographs. Stored rather than "
            "read from `DEFAULT_THRESHOLDS` at report time so a recommendation stays "
            "reproducible under §57: M7/M8's calibration moves these, and a moved "
            "threshold must produce new analyses rather than reinterpret old ones. "
            "**Never accepted from a client** — a policy is not a card's costs."
        ),
    ),
    sa.Column(
        "minimum_grade_confidence",
        sa.Double(),
        nullable=False,
        comment=(
            "#64's gate on the grading company's model alone. Separate from "
            "`minimum_figure_confidence` on purpose: #59 folds the price's confidence "
            "into the expectation, so gating only the expectation would let a fresh "
            "price ladder rescue a model nobody measured."
        ),
    ),
    sa.Column(
        "minimum_figure_confidence",
        sa.Double(),
        nullable=False,
        comment=(
            "#64's gate on the graded expectation. The lowest of the three by design: "
            "#59's confidence is a product of three numbers in [0, 1] and compounds "
            "faster than intuition expects."
        ),
    ),
    sa.Column(
        "maximum_unpriced_probability",
        sa.Double(),
        nullable=False,
        comment=(
            "How much of the grade distribution may have no price and the answer still "
            "be reported — #64's ceiling on #59's `unpriced_probability`. A maximum "
            "where the others are minimums, and inclusive like them."
        ),
    ),
    _amount(
        "minimum_incremental_profit",
        (
            "What grading must be expected to clear before it is recommended — #64's "
            "**margin of safety, not a sign test**. A thin positive expected profit is "
            "`do_not_grade`, because +2.00 of edge on a 60.00 submission is a known "
            "answer and the answer is no; the `insufficient_information` admission "
            "stays reserved for inadequate data."
        ),
    ),
    sa.CheckConstraint(one_of("currency", Currency), name="currency_is_a_known_currency"),
    _non_negative("acquisition_cost", nullable=True),
    _non_negative("grading_fee"),
    _non_negative("outbound_shipping"),
    _non_negative("return_shipping"),
    _non_negative("insurance"),
    _non_negative("miscellaneous"),
    _non_negative("selling_fee_flat"),
    _non_negative("minimum_incremental_profit", as_name="minimum_profit_is_not_negative"),
    # A percentage that slipped through as a proportion charges the user a fee a
    # hundred times too large, on the one figure the whole recommendation turns
    # on. `SellingFee` refuses it too; neither is the only guard.
    sa.CheckConstraint(
        "selling_fee_rate >= 0 AND selling_fee_rate <= 1",
        name="selling_fee_rate_is_a_proportion",
    ),
    _unit_interval("minimum_image_quality", "image_quality_in_unit_range"),
    _unit_interval("minimum_grade_confidence", "grade_confidence_in_unit_range"),
    _unit_interval("minimum_figure_confidence", "figure_confidence_in_unit_range"),
    _unit_interval("maximum_unpriced_probability", "unpriced_probability_in_unit_range"),
    # `cardinality`, not `array_length`: `array_length('{}', 1)` is NULL, and a
    # NULL CHECK *passes*. An empty selection would compare no companies and make
    # every §44 answer `no_company_can_be_ranked`.
    sa.CheckConstraint(
        "cardinality(grading_companies) >= 1",
        name="at_least_one_grading_company",
    ),
    # No index. A configuration is reached by primary key from
    # `analyses.economic_configuration_id` and by nothing else; there is no query
    # that lists them, and #66 has no screen that would.
    comment=(
        "One immutable economic configuration — spec §46's cost line items, §45's "
        "optional acquisition cost, the companies to compare, §43's optimization mode "
        "and #64's recommendation thresholds. Referenced by "
        "`analyses.economic_configuration_id` (spec §57), so an analysis can be "
        "re-derived with the exact numbers it was computed under. Write-once: "
        "trg_economic_configurations_immutable refuses an UPDATE, and re-running with "
        "different costs is a new analysis rather than an edit. **Nothing here is a "
        "total** — §47's future dimensions attach to individual lines."
    ),
)


TABLES: Final = (economic_configurations,)


# ---------------------------------------------------------------------------
# Write-once, as a database guarantee rather than a promise
# ---------------------------------------------------------------------------
# The issue is explicit: "once an analysis references a configuration, that
# configuration is immutable — re-running with different costs creates a new
# analysis". An UPDATE would rewrite the numbers a past recommendation was
# computed from while leaving the recommendation in place, which is spec §57's
# whole subject.
#
# **UPDATE only, and deliberately not DELETE** — following
# `trg_market_snapshots_immutable` rather than `trg_grading_rules_immutable`. A
# configuration holds what a user said they paid for their card, so spec §54's
# retention sweep must be able to delete one when its session expires; see
# `tcg_api.analysis.retention`. A row an analysis still references is undeletable
# anyway, through the foreign key's RESTRICT rather than through a trigger.
#
# Alembic compares no triggers, so nothing warns if this and the migration drift
# apart. `test_economics_schema.py` asserts an UPDATE is actually refused against
# a real database; that test is the only guard there is.
def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here."""
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time. Do not "simplify" it back.
_IMMUTABLE_FUNCTION: Final = _ddl(
    """
    CREATE OR REPLACE FUNCTION economic_configuration_is_immutable()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'economic configuration ' || OLD.id || ' is immutable',
            HINT    = 'Run a new analysis rather than rewriting the numbers an old one used.';
    END;
    $$;
    """
)

_IMMUTABLE_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_economic_configurations_immutable
    BEFORE UPDATE ON economic_configurations
    FOR EACH ROW EXECUTE FUNCTION economic_configuration_is_immutable();
    """
)

# Two statements, two DDL objects: the asyncpg driver prepares each statement it
# is handed, and a prepared statement may not contain more than one.
_DROP_IMMUTABLE_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_economic_configurations_immutable ON economic_configurations"
)

_DROP_IMMUTABLE_FUNCTION: Final = _ddl(
    "DROP FUNCTION IF EXISTS economic_configuration_is_immutable()"
)

sa.event.listen(
    economic_configurations,
    "after_create",
    _IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
sa.event.listen(
    economic_configurations,
    "after_create",
    _IMMUTABLE_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    economic_configurations,
    "before_drop",
    _DROP_IMMUTABLE_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    economic_configurations,
    "before_drop",
    _DROP_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
