"""add the market data schema

Spec §35 names two tables: one row per price a provider reported for a card at a
moment, and one row per provider recording what this project is licensed to do
with those prices. Nothing has persisted a price until now — `PriceObservation`
validates one in memory and has nowhere to go — and `analyses.market_snapshot_id`
has pointed at nothing since the reproducibility record landed.

The shape and the reasoning live in
`services/api/src/tcg_api/market/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Five things worth knowing before reading the DDL:

* **`market_type` is a stored generated column, not a written one.**
  `PriceObservation.market_type` already derives it from `grading_company`, so
  generating it here makes a row claiming `raw` while carrying a grading company
  unrepresentable rather than merely refused. `cards.card_number_key` is the
  precedent. One CHECK is still needed, because the generator reads only
  `grading_company` and a graded row could otherwise carry no grade.
* **`grade` is text, and its CHECK is the grade grammar rather than any
  company's scale.** The pattern reproduces `tcg_domain.Grade` exactly — half
  steps in [0, 10], plus §24's collapsed tails. A per-company CHECK would make a
  fourth company, or a scale revision, cost a migration of this table.
* **`grading_company` does take a CHECK built from `GradingCompany`**, where
  `grading_rules.company` deliberately does not: a price row is data *about* a
  company V1 ships, where a rules row is the company's own record.
* **`price` is NUMERIC(12, 2)**, never floating point, and two places to match
  `Money`'s own quantisation so a round trip changes no value.
* **Append-only is a trigger, guarding UPDATE and not DELETE** — following
  `trg_analyses_reproducibility_immutable` rather than
  `trg_grading_rules_immutable`. A daily refresh over the whole catalog will
  eventually need pruning; a provider row anything references is already
  undeletable through the foreign key's RESTRICT. One function serves both
  tables, naming itself with `TG_TABLE_NAME`.

No rows are inserted here. A `market_providers` row asserts a licensing
determination, and ADR 0006 gates commercial use on a subscription that is not
yet active — writing `commercial_use = true` today would be a false record in the
one table that exists to be truthful about exactly that. The provider adapter
registers its own row.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_market_schema.py` asserts an
UPDATE is actually refused; that test is the only guard there is.

Revision ID: b5bca50f46c0
Revises: 50c399cb7b9b
Create Date: 2026-08-24 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5bca50f46c0"
down_revision: str | None = "50c399cb7b9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

NO_METADATA = sa.text("'{}'::jsonb")

# Written out as literals rather than built from the enums the table module uses.
# A migration is a snapshot of what was applied; `test_market_tables.py` checks
# that the two still agree.
MARKET_TYPE_EXPRESSION = "CASE WHEN grading_company IS NULL THEN 'raw' ELSE 'graded' END"
GRADING_COMPANIES = "'psa', 'tag', 'bgs'"
SLUG_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$"
GRADE_KEY_PATTERN = r"^(10|[0-9](\.5)?)(_or_(lower|higher))?$"
CURRENCY_PATTERN = "^[A-Z]{3}$"

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: the same statement is declared through `sa.DDL` in `tables.py`, and
# `sa.DDL` runs its statement through Python's `%` interpolation, so a format
# specifier in the body fails at compile time. Kept identical here so the two
# copies can be diffed by eye.
#
# `restrict_violation` is SQLSTATE class 23, which SQLAlchemy surfaces as an
# `IntegrityError` — the same shape as every other constraint in this schema,
# rather than an `InternalError` a caller would have to special-case.
IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION market_rows_are_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = TG_TABLE_NAME || ' is append-only: '
                  || TG_OP || ' was refused',
        HINT    = 'Record a new row rather than rewriting one.';
END;
$$;
"""

PROVIDERS_TRIGGER = """
CREATE TRIGGER trg_market_providers_immutable
BEFORE UPDATE ON market_providers
FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
"""

OBSERVATIONS_TRIGGER = """
CREATE TRIGGER trg_market_observations_immutable
BEFORE UPDATE ON market_observations
FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
"""


def upgrade() -> None:
    op.create_table(
        "market_providers",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Ours, not the provider's. What `market_observations.provider_id` names.",
        ),
        sa.Column(
            "slug",
            PRINTED,
            nullable=False,
            comment=(
                "The provider's lowercase slug — 'pokepricetracker', 'manual'. This is the "
                "string `PriceObservation.provider` carries and the one "
                "`card_external_ids.provider` already spells a source with; the display "
                "name lives in `name` beside it."
            ),
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment=(
                "The provider's name as its own terms of service spell it — ADR 0006 binds "
                "'PokePriceTracker', never 'PokemonPriceTracker'. This is the party the "
                "licence fields below describe, so it is recorded verbatim rather than "
                "derived from the slug."
            ),
        ),
        sa.Column(
            "version",
            PRINTED,
            nullable=True,
            comment=(
                "The version the *provider* publishes for its own API or data. NULL for "
                "every candidate M3 surveyed, none of which publishes one — a fact, not a "
                "gap. Distinct from §36's snapshot `data_version`, which is this "
                "repository's identifier for one ingestion run."
            ),
        ),
        sa.Column(
            "license",
            sa.Text(),
            nullable=False,
            comment=(
                "What the provider's terms licence, in this project's own words — an "
                "enforcement field, not documentation. Never empty: a provider whose terms "
                "have not been read has no row here."
            ),
        ),
        sa.Column(
            "commercial_use",
            sa.Boolean(),
            nullable=False,
            comment=(
                "Whether those terms permit commercial use of the data. No server default, "
                "deliberately: ADR 0006 records that two of the three shortlisted "
                "candidates could not honestly fill this in, and an unclear determination "
                "must never default to true."
            ),
        ),
        sa.Column(
            "terms_reference",
            sa.Text(),
            nullable=False,
            comment=(
                "Where those terms were read, as a URL a human can open. The evidence "
                "standard M3's rubric fixed: a licensing claim is evidenced by the terms "
                "text itself, never by a marketing page or a support reply."
            ),
        ),
        sa.Column(
            "verified_on",
            sa.Date(),
            nullable=False,
            comment=(
                "When those terms were last read. Beyond §35's list, and the field ADR "
                "0006's ninety-day rule applies to — the same pair `grading_rules` draws "
                "between `verified_on` and `created_at`. A record older than ninety days "
                "is re-read before anything relies on it."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When this row was written, as distinct from when the terms were read.",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_providers"),
        # `NULLS NOT DISTINCT` is what makes this mean anything today, since no
        # provider publishes a version: it collapses to "one row per slug".
        sa.UniqueConstraint(
            "slug",
            "version",
            name="uq_market_providers_slug_version",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_is_a_lowercase_slug"),
        comment=(
            "One market-data provider, and what this project is licensed to do with its "
            "data — spec §35. Append-only, enforced by trg_market_providers_immutable: an "
            "UPDATE to `commercial_use` would retroactively relicense every observation "
            "gathered under the old terms."
        ),
    )

    op.create_table(
        "market_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "card_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "RESTRICT, as `analyses.card_id` is: removing a card from the catalog must "
                "not silently take its price history with it. The ingestion worker resolves "
                "this from the `CardReference` a `PriceObservation` carries."
            ),
        ),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "Which provider reported this price, and therefore which licence it was "
                "gathered under. RESTRICT is also what makes a provider row undeletable "
                "once anything references it, so no trigger needs to guard that."
            ),
        ),
        sa.Column(
            "grading_company",
            PRINTED,
            nullable=True,
            comment=(
                "The company that graded the card, for a graded price; NULL for a raw one. "
                "Unlike `grading_rules.company` this carries a CHECK: a price row is data "
                "*about* a company V1 ships, where a rules row is the company's own record."
            ),
        ),
        sa.Column(
            "grade",
            PRINTED,
            nullable=True,
            comment=(
                "The grade it was graded at, as a text key — never a number. BGS issues a "
                "9.5 where PSA and TAG do not, all three issue half grades elsewhere, and "
                "§24's collapsed tails ('7_or_lower') are legal keys too. `COLLATE \"C\"` "
                "so ordering means the same thing on every server this runs against."
            ),
        ),
        sa.Column(
            "market_type",
            sa.Text(),
            sa.Computed(MARKET_TYPE_EXPRESSION, persisted=True),
            nullable=False,
            comment=(
                "§35's raw/graded discriminator, generated from `grading_company` rather "
                "than written. A row claiming 'raw' while carrying a grading company is "
                "therefore not representable. PostgreSQL refuses an INSERT that names this "
                "column, which is deliberate: `PriceObservation.market_type` is the only "
                "place the rule is stated, and this is that statement in SQL."
            ),
        ),
        sa.Column(
            "currency",
            sa.Text(),
            nullable=False,
            comment=(
                "The ISO 4217 code the provider quoted in. V1 reports SGD and converts "
                "nothing, but the selected provider prices in USD — an observation records "
                "what was said, and normalization owns the conversion. Not COLLATE C: "
                "compared for equality only."
            ),
        ),
        sa.Column(
            "price",
            sa.Numeric(12, 2),
            nullable=False,
            comment=(
                "The price, exactly. Two decimal places, matching `Money`'s own "
                "quantisation, so a round trip through the database changes no value. Zero "
                "is a legal observation — a card nobody will pay for really is worth "
                "nothing, and that is the one value which must never be confused with an "
                "absent price. Negative is not a price anybody ever saw."
            ),
        ),
        sa.Column(
            "confidence",
            sa.Double(),
            nullable=False,
            comment=(
                "How much this single observation is worth, in [0, 1] — the provider's own "
                "signal, from sample size and spread. Beyond §35's list, and a column "
                "rather than a key in `metadata` for the reason `images.quality_score` is "
                "one: `PriceObservation.confidence` is required and range-validated, and a "
                "mandatory field in an untyped bag quietly becomes optional. **Not "
                "staleness** — §38's `price_age` is a function of `observed_at` and the "
                "moment of asking."
            ),
        ),
        sa.Column(
            "observed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment=(
                "When the price was seen, as the provider reports it. Timezone-aware "
                "throughout: a naive timestamp would make `price_age` silently wrong."
            ),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=NO_METADATA,
            nullable=False,
            comment=(
                "Whatever the provider reported that has no column of its own — sample "
                "size, listing counts, its own identifiers. Never the price, the currency "
                "or the confidence, each of which has a column and a constraint."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=(
                "When this row was written, as distinct from `observed_at` — a backfilled "
                "observation is seen long before it is stored. Snapshot generation needs "
                "that distinction: a snapshot resolved on `observed_at` alone could be "
                "joined retroactively by a late arrival, which would make an immutable "
                "snapshot resolve differently on two readings of the same data."
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_observations"),
        # The other half of the generated `market_type`: the generator reads only
        # `grading_company`, so without this a row could claim 'graded' and carry
        # no grade — which is not a price for anything.
        sa.CheckConstraint(
            "(grading_company IS NULL) = (grade IS NULL)",
            name="graded_rows_carry_a_company_and_a_grade",
        ),
        sa.CheckConstraint(
            f"grading_company IS NULL OR grading_company IN ({GRADING_COMPANIES})",
            name="grading_company_is_a_supported_company",
        ),
        sa.CheckConstraint(
            f"grade IS NULL OR grade ~ '{GRADE_KEY_PATTERN}'",
            name="grade_is_a_grade_key",
        ),
        sa.CheckConstraint(f"currency ~ '{CURRENCY_PATTERN}'", name="currency_is_an_iso_4217_code"),
        sa.CheckConstraint("price >= 0", name="price_is_not_negative"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_is_a_unit_interval"
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name="fk_market_observations_card_id_cards",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_providers.id"],
            name="fk_market_observations_provider_id_market_providers",
            ondelete="RESTRICT",
        ),
        comment=(
            "One price, for one card, from one provider, seen at one moment — spec §35. "
            "Append-only, enforced by trg_market_observations_immutable: a corrected price "
            "is a new observation, which is what makes price history honest. `market_type` "
            "is generated from `grading_company`; see tcg_api.market.tables."
        ),
    )

    # Named explicitly and shortened: the naming convention's own rendering is
    # exactly 63 bytes, which is PostgreSQL's identifier limit.
    op.create_index(
        "ix_market_observations_card_company_grade_observed_at",
        "market_observations",
        ["card_id", "grading_company", "grade", "observed_at"],
    )
    op.create_index(
        "ix_market_observations_observed_at",
        "market_observations",
        ["observed_at"],
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(PROVIDERS_TRIGGER)
    op.execute(OBSERVATIONS_TRIGGER)


def downgrade() -> None:
    # `DROP TABLE` would take each trigger with it, but not the function they
    # share. Naming all five keeps the reversal exact and leaves nothing
    # orphaned in the catalog for the next `upgrade` to collide with.
    op.execute("DROP TRIGGER IF EXISTS trg_market_observations_immutable ON market_observations")
    op.execute("DROP TRIGGER IF EXISTS trg_market_providers_immutable ON market_providers")
    op.drop_table("market_observations")
    op.drop_table("market_providers")
    op.execute("DROP FUNCTION IF EXISTS market_rows_are_immutable()")
