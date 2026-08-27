"""add the economic configuration schema

Spec §57 requires every analysis to record the economic configuration it was
computed under, so a historical recommendation can be re-derived with the exact
costs and thresholds it used rather than with whatever is configured when
somebody re-reads it. `analyses.economic_configuration_id` has carried no
foreign key since the reproducibility record landed, because the table it points
at did not exist; this revision creates it and adds the key.

The shape and the reasoning live in
`services/api/src/tcg_api/economics/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Four things worth knowing before reading the DDL:

* **There is no total.** §46's costs are stored as named line items and nothing
  computes a grand total, because §47's future dimensions — country, tax,
  service tier, shipping provider — attach to *individual* lines. A
  `total_costs` column would make every one of them a rewrite.
* **`acquisition_cost` is nullable, and `0.00` is a real value.** §45 makes it
  optional user input and forbids inferring it. A NOT NULL column defaulting to
  zero would turn "I don't remember what I paid" into "it was free", which is
  the fabrication the specification is explicit about.
* **The five thresholds are policy, stored per configuration.** They are written
  from the engine's `DEFAULT_THRESHOLDS` and never accepted from a client.
  Storing them is what makes a recommendation reproducible when M7/M8's
  calibration moves them: recalibration then produces new configurations rather
  than silently reinterpreting old answers.
* **`optimization_mode` and `grading_companies` carry no membership CHECK.** §43
  requires the architecture to allow future optimization modes, and
  `grading_rules.company` already sets the precedent that a fourth grading
  company is one adapter and no migration. Both are validated at the HTTP
  boundary against the same sources `GET /grading-companies` serves.

The write-once trigger gets its own function rather than reusing
`market_rows_are_immutable()`: that one names its table with `TG_TABLE_NAME` and
says "append-only", which is true of a price history and not of this — a
configuration is written once with an analysis and is deleted with it. The
message here names the row and points at running a new analysis.

**UPDATE only, deliberately not DELETE.** A configuration holds what a user said
they paid for their card, so spec §54's retention sweep has to be able to remove
one when its session expires. A row an analysis still references is undeletable
anyway, through the foreign key's RESTRICT.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_economics_schema.py` asserts an
UPDATE is actually refused; that test is the only guard there is.

Revision ID: 9d2f61c47ab3
Revises: 3ac71e5d92f8
Create Date: 2026-08-27 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9d2f61c47ab3"
down_revision: str | None = "3ac71e5d92f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


IMMUTABLE_FUNCTION = """
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

IMMUTABLE_TRIGGER = """
CREATE TRIGGER trg_economic_configurations_immutable
BEFORE UPDATE ON economic_configurations
FOR EACH ROW EXECUTE FUNCTION economic_configuration_is_immutable();
"""

CONFIGURATION_COMMENT = (
    "Which immutable economic configuration the economics were computed under — "
    "spec §57, recorded when the user supplied it rather than when the run "
    "claimed the analysis, because it is user input and does not exist at claim "
    "time. RESTRICT for `market_snapshot_id`'s reason: a configuration *is* the "
    "numbers, so one an analysis references must stay resolvable for as long as "
    "the analysis does. NULL until the user has configured the economics."
)

PREVIOUS_CONFIGURATION_COMMENT = (
    "The fee and cost configuration used. No foreign key yet — the table it "
    "will point at arrives with the economics milestone, and adding the "
    "constraint then is cheaper than changing this column's type."
)


def upgrade() -> None:
    op.create_table(
        "economic_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.Text(collation="C"),
            server_default=sa.text("'SGD'"),
            nullable=False,
            comment=(
                "What every amount in this row is denominated in. Constrained to what "
                "`tcg_domain.money.Currency` models, deliberately unlike "
                "`market_observations.currency`, which admits any ISO 4217 code because an "
                "observation records what a provider actually said. This row records what a "
                "user was quoted in, and V1 quotes in SGD (§46); #53 owns conversion."
            ),
        ),
        sa.Column(
            "acquisition_cost",
            sa.Numeric(12, 2),
            nullable=True,
            comment=(
                "What the user paid for the card — spec §45's optional input. **NULL means "
                "they did not say, and `0.00` is a real acquisition cost**: a raffle win, a "
                "pack out of somebody else's box. The two are different answers and #61 "
                "reports the first as `acquisition_cost_not_supplied` rather than as a "
                "number. Never inferred, and never filled in from the raw market price — "
                "that answers 'what if you had bought it today?', which is a different "
                "question."
            ),
        ),
        sa.Column(
            "grading_fee",
            sa.Numeric(12, 2),
            nullable=False,
            comment="§46's `grading_fee`: the company's charge per card for the chosen tier.",
        ),
        sa.Column(
            "outbound_shipping",
            sa.Numeric(12, 2),
            nullable=False,
            comment="§46's `outbound_shipping`: getting the card to the grader.",
        ),
        sa.Column(
            "return_shipping",
            sa.Numeric(12, 2),
            nullable=False,
            comment=(
                "§46's `return_shipping`: getting it back. Separate from outbound because "
                "the two differ — return is insured and often faster — and §47 will make "
                "them differ more."
            ),
        ),
        sa.Column(
            "insurance",
            sa.Numeric(12, 2),
            nullable=False,
            comment="§46's `insurance`: cover for the round trip.",
        ),
        sa.Column(
            "miscellaneous",
            sa.Numeric(12, 2),
            nullable=False,
            comment="§46's `miscellaneous`: sleeves, semi-rigids, a courier surcharge.",
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
        sa.Column(
            "selling_fee_flat",
            sa.Numeric(12, 2),
            nullable=False,
            comment=(
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
            sa.Text(collation="C"),
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
        sa.Column(
            "minimum_incremental_profit",
            sa.Numeric(12, 2),
            nullable=False,
            comment=(
                "What grading must be expected to clear before it is recommended — #64's "
                "**margin of safety, not a sign test**. A thin positive expected profit is "
                "`do_not_grade`, because +2.00 of edge on a 60.00 submission is a known "
                "answer and the answer is no; the `insufficient_information` admission "
                "stays reserved for inadequate data."
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_economic_configurations"),
        sa.CheckConstraint("currency IN ('SGD')", name="currency_is_a_known_currency"),
        sa.CheckConstraint(
            "acquisition_cost IS NULL OR acquisition_cost >= 0",
            name="acquisition_cost_is_not_negative",
        ),
        sa.CheckConstraint("grading_fee >= 0", name="grading_fee_is_not_negative"),
        sa.CheckConstraint("outbound_shipping >= 0", name="outbound_shipping_is_not_negative"),
        sa.CheckConstraint("return_shipping >= 0", name="return_shipping_is_not_negative"),
        sa.CheckConstraint("insurance >= 0", name="insurance_is_not_negative"),
        sa.CheckConstraint("miscellaneous >= 0", name="miscellaneous_is_not_negative"),
        sa.CheckConstraint("selling_fee_flat >= 0", name="selling_fee_flat_is_not_negative"),
        sa.CheckConstraint(
            "minimum_incremental_profit >= 0",
            name="minimum_profit_is_not_negative",
        ),
        sa.CheckConstraint(
            "selling_fee_rate >= 0 AND selling_fee_rate <= 1",
            name="selling_fee_rate_is_a_proportion",
        ),
        sa.CheckConstraint(
            "minimum_image_quality >= 0 AND minimum_image_quality <= 1",
            name="image_quality_in_unit_range",
        ),
        sa.CheckConstraint(
            "minimum_grade_confidence >= 0 AND minimum_grade_confidence <= 1",
            name="grade_confidence_in_unit_range",
        ),
        sa.CheckConstraint(
            "minimum_figure_confidence >= 0 AND minimum_figure_confidence <= 1",
            name="figure_confidence_in_unit_range",
        ),
        sa.CheckConstraint(
            "maximum_unpriced_probability >= 0 AND maximum_unpriced_probability <= 1",
            name="unpriced_probability_in_unit_range",
        ),
        sa.CheckConstraint(
            "cardinality(grading_companies) >= 1",
            name="at_least_one_grading_company",
        ),
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

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(IMMUTABLE_TRIGGER)

    op.create_foreign_key(
        "fk_analyses_economic_configuration_id_economic_configurations",
        "analyses",
        "economic_configurations",
        ["economic_configuration_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Alembic compares column comments, so a reworded comment in `tables.py` and
    # not here is a drift the guard fails on.
    op.alter_column(
        "analyses",
        "economic_configuration_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=CONFIGURATION_COMMENT,
        existing_comment=PREVIOUS_CONFIGURATION_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "economic_configuration_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=PREVIOUS_CONFIGURATION_COMMENT,
        existing_comment=CONFIGURATION_COMMENT,
    )
    op.drop_constraint(
        "fk_analyses_economic_configuration_id_economic_configurations",
        "analyses",
        type_="foreignkey",
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_economic_configurations_immutable ON economic_configurations"
    )
    op.execute("DROP FUNCTION IF EXISTS economic_configuration_is_immutable()")
    op.drop_table("economic_configurations")
