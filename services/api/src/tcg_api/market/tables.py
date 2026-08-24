"""Spec §35's `market_providers` and `market_observations`, as SQLAlchemy Core.

Two tables. One row per price a provider reported for a card at a moment, and
one row per provider recording **what this project is licensed to do with those
prices**. `license`, `commercial_use` and `terms_reference` are enforcement
fields rather than documentation: ADR 0006 relies on one right — derived data,
its risk R5 — that no shortlisted candidate grants expressly, and gates
commercial use on an active Business plan. A licensing question years from now
has to have an answer in the database rather than in someone's memory.

`packages/market-data` holds the same observation as a frozen dataclass
(`tcg_market_data.PriceObservation`) and validates it on construction. This is
where one goes so that #51's snapshots, #55's price age and the economic engine
can read a price nobody has to fetch again. **Core, not ORM**, and the entity is
not redeclared here — same direction as the catalog and grading adapters.

Five decisions, each of which binds a later milestone:

* **`market_type` is a stored generated column, not a written one.** §35 lists
  it, and `PriceObservation.market_type` already derives it from
  `grading_company` with the note that "two fields that must agree is precisely
  the drift #50 has to police in SQL". Generating it means a row claiming `raw`
  while carrying a grading company is not representable at all, rather than
  refused by a constraint somebody could later relax. `cards.card_number_key` is
  the precedent. One CHECK is still needed — the generator reads only
  `grading_company`, so a graded row could otherwise arrive with no grade.

* **`grade` is text, and its CHECK is the grade *grammar*, never a company's
  scale.** The pattern reproduces `tcg_domain.Grade` exactly: half-point steps
  within [0, 10], plus §24's collapsed tails (`7_or_lower`). It deliberately
  does not know that PSA issues no 9.5 and BGS does — a per-company CHECK would
  make a fourth company, or a scale revision, cost a migration of this table.
  `tcg_market_data.validated_grade_key` is the per-company guard, and
  `tcg_market_data.errors` already says neither substitutes for the other.

* **`grading_company` *does* take a CHECK built from `GradingCompany`**, where
  `grading_rules.company` deliberately does not. A price row is data *about* a
  company V1 ships; a `grading_rules` row is the company's own record.

* **`currency` admits any ISO 4217 code, not only SGD.** V1 reports SGD and
  converts nothing, but ADR 0006's provider prices in USD, and an observation
  records what the provider actually said. A column admitting one value would be
  SGD hard-coded rather than §35's currency column; #53 owns normalization.

* **`price` is `NUMERIC(12, 2)`.** Never floating point: the economic engine
  sums fees, shipping and proceeds, and a value that starts as 0.1 is already
  wrong before any arithmetic happens. Two places matches `Money`'s own
  quantisation, so a round trip through the database changes nothing.

Both tables are append-only, and say so in the database rather than in a
comment — see the trigger at the foot of this module.
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_grading_companies import GradingCompany
from tcg_market_data import MarketType

# `market_observations.card_id` points into the catalog, so the catalog is a
# hard dependency of this module rather than merely of the migration
# environment: a `sa.ForeignKey` resolves against the `MetaData` it is attached
# to, and without `cards` on it `CreateTable(market_observations)` raises
# NoReferencedTableError. Referenced as a column object rather than by the
# string "cards.id" so the dependency is visible to a reader and to mypy.
from tcg_api.catalog.tables import cards
from tcg_api.tables import NO_METADATA as _NO_METADATA
from tcg_api.tables import PRINTED as _PRINTED
from tcg_api.tables import metadata, one_of

__all__ = ["TABLES", "market_observations", "market_providers"]


#: A lowercase slug, mirroring `tcg_domain.card.validated_slug`'s grammar so the
#: database refuses exactly what the domain refuses.
_SLUG_PATTERN: Final = "^[a-z0-9]+(-[a-z0-9]+)*$"

#: `tcg_domain.Grade`'s key grammar, written as one regular expression: a whole
#: or half grade in [0, 10], optionally collapsed into one of §24's tails. `10`
#: is spelled out because `10.5` is not a grade and `[0-9](\.5)?` cannot say so.
_GRADE_KEY_PATTERN: Final = r"^(10|[0-9](\.5)?)(_or_(lower|higher))?$"

#: ISO 4217. Deliberately a shape rather than a list: the alternative is a CHECK
#: that has to be migrated when a currency is added, for a column whose whole
#: purpose is to record what a provider reported.
_CURRENCY_PATTERN: Final = "^[A-Z]{3}$"

#: §35's `market_type`, derived from `grading_company` in SQL exactly as
#: `PriceObservation.market_type` derives it in Python. Built from the enum
#: rather than retyped, so a third member could not silently go unrepresented.
_MARKET_TYPE_EXPRESSION: Final = (
    f"CASE WHEN grading_company IS NULL "
    f"THEN '{MarketType.RAW.value}' ELSE '{MarketType.GRADED.value}' END"
)


market_providers = sa.Table(
    "market_providers",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(),
        primary_key=True,
        comment="Ours, not the provider's. What `market_observations.provider_id` names.",
    ),
    sa.Column(
        "slug",
        _PRINTED,
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
        _PRINTED,
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
        server_default=sa.func.now(),
        nullable=False,
        comment="When this row was written, as distinct from when the terms were read.",
    ),
    sa.PrimaryKeyConstraint("id", name="pk_market_providers"),
    # One row per provider per published version. `NULLS NOT DISTINCT` is what
    # makes it mean anything today, since no provider publishes a version: it
    # collapses to "one row per slug" and refuses a second undated record for a
    # provider that already has one. The day a provider does publish a version,
    # a new one is a new row and nothing here changes. PostgreSQL 15+; both
    # Compose and CI run 17.
    sa.UniqueConstraint(
        "slug",
        "version",
        name="uq_market_providers_slug_version",
        postgresql_nulls_not_distinct=True,
    ),
    sa.CheckConstraint(
        f"slug ~ '{_SLUG_PATTERN}'",
        name="slug_is_a_lowercase_slug",
    ),
    comment=(
        "One market-data provider, and what this project is licensed to do with its "
        "data — spec §35. Append-only, enforced by trg_market_providers_immutable: an "
        "UPDATE to `commercial_use` would retroactively relicense every observation "
        "gathered under the old terms."
    ),
)


market_observations = sa.Table(
    "market_observations",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "card_id",
        sa.Uuid(),
        sa.ForeignKey(
            cards.c.id,
            ondelete="RESTRICT",
            name="fk_market_observations_card_id_cards",
        ),
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
        sa.ForeignKey(
            "market_providers.id",
            ondelete="RESTRICT",
            name="fk_market_observations_provider_id_market_providers",
        ),
        nullable=False,
        comment=(
            "Which provider reported this price, and therefore which licence it was "
            "gathered under. RESTRICT is also what makes a provider row undeletable "
            "once anything references it, so no trigger needs to guard that."
        ),
    ),
    sa.Column(
        "grading_company",
        _PRINTED,
        nullable=True,
        comment=(
            "The company that graded the card, for a graded price; NULL for a raw one. "
            "Unlike `grading_rules.company` this carries a CHECK: a price row is data "
            "*about* a company V1 ships, where a rules row is the company's own record."
        ),
    ),
    sa.Column(
        "grade",
        _PRINTED,
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
        sa.Computed(_MARKET_TYPE_EXPRESSION, persisted=True),
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
        server_default=_NO_METADATA,
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
        server_default=sa.func.now(),
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
    # This is the other half of the generated `market_type`. The generator reads
    # only `grading_company`, so without this a row could claim 'graded' and
    # carry no grade — which is not a price for anything.
    sa.CheckConstraint(
        "(grading_company IS NULL) = (grade IS NULL)",
        name="graded_rows_carry_a_company_and_a_grade",
    ),
    sa.CheckConstraint(
        f"grading_company IS NULL OR {one_of('grading_company', GradingCompany)}",
        name="grading_company_is_a_supported_company",
    ),
    sa.CheckConstraint(
        f"grade IS NULL OR grade ~ '{_GRADE_KEY_PATTERN}'",
        name="grade_is_a_grade_key",
    ),
    sa.CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_is_an_iso_4217_code"),
    sa.CheckConstraint("price >= 0", name="price_is_not_negative"),
    sa.CheckConstraint(
        "confidence >= 0 AND confidence <= 1",
        name="confidence_is_a_unit_interval",
    ),
    # Two indexes, and between them they serve all three lookups the milestone
    # needs. The leading column answers "every price for this card"; the prefix
    # answers "(card, company, grade)"; and "the latest graded price for this
    # card" is a backward scan of the tail, so no DESC variant is needed —
    # measured, not assumed, against 60,000 rows.
    #
    # ponytail: the *raw* form of that last query is a bitmap scan plus a sort,
    # because `grading_company IS NULL` is not an equality condition and cannot
    # drive an ordered scan. The sort is over one card's history — a year of
    # daily ingestion is ~365 rows — so it is not worth a third index. If it ever
    # is, that is a partial index on (card_id, observed_at) WHERE grading_company
    # IS NULL, not a wider version of this one.
    #
    # Named explicitly and shortened — the convention's own rendering
    # (`..._card_id_grading_company_grade_observed_at`) is exactly 63 bytes,
    # which is PostgreSQL's identifier limit, so it would truncate the moment
    # anything about it changed.
    sa.Index(
        "ix_market_observations_card_company_grade_observed_at",
        "card_id",
        "grading_company",
        "grade",
        "observed_at",
    ),
    # History across cards, and what #51 resolves a snapshot with.
    sa.Index("ix_market_observations_observed_at", "observed_at"),
    # ponytail: no index on `provider_id`. The only thing that would use one is
    # the FK check on a provider DELETE, against a table holding one row. Add
    # one if a query ever filters by provider.
    comment=(
        "One price, for one card, from one provider, seen at one moment — spec §35. "
        "Append-only, enforced by trg_market_observations_immutable: a corrected price "
        "is a new observation, which is what makes price history honest. `market_type` "
        "is generated from `grading_company`; see tcg_api.market.tables."
    ),
)


TABLES: Final = (market_providers, market_observations)


# ---------------------------------------------------------------------------
# Append-only, as a database guarantee rather than a promise
# ---------------------------------------------------------------------------
# Two reasons, one mechanism. An UPDATE to a price destroys the history §36's
# snapshots and §38's price age are computed from, and the issue is explicit
# that "a corrected price is a new observation, not an update". An UPDATE to
# `market_providers.commercial_use` is worse: it retroactively relicenses every
# observation already gathered under the old terms, in the one table that exists
# to be truthful about exactly that.
#
# **UPDATE only, and deliberately not DELETE** — following
# `trg_analyses_reproducibility_immutable` rather than
# `trg_grading_rules_immutable`. A daily refresh over 49,399 cards is millions of
# rows a year and will eventually need pruning; #51's non-goals already speak of
# a retention policy. A provider row that anything references is already
# undeletable, through the FK's RESTRICT rather than through a trigger.
#
# One function, two triggers. `TG_TABLE_NAME` makes the message name the table,
# so there is no second near-identical copy to keep in step.
#
# Three things worth knowing before changing this:
#
# * `plpgsql` is not an extension this schema installs. It ships enabled in
#   every stock PostgreSQL and in Supabase, and no CREATE EXTENSION is issued.
# * TRUNCATE bypasses row-level triggers, deliberately — that is what lets the
#   integration fixtures reset.
# * Alembic's `compare_metadata` does not compare triggers, so nothing will warn
#   if this and the migration drift apart. `test_market_schema.py` asserts an
#   UPDATE is actually refused against a real database; that test is the only
#   guard there is.
# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time rather than at runtime.
# Do not "simplify" this back into the printf form.
def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here."""
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


_IMMUTABLE_FUNCTION: Final = _ddl(
    """
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
)

_PROVIDERS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_market_providers_immutable
    BEFORE UPDATE ON market_providers
    FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
    """
)

_OBSERVATIONS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_market_observations_immutable
    BEFORE UPDATE ON market_observations
    FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
    """
)

# Two statements, two DDL objects: the asyncpg driver prepares each statement it
# is handed, and a prepared statement may not contain more than one.
_DROP_PROVIDERS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_market_providers_immutable ON market_providers"
)

_DROP_OBSERVATIONS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_market_observations_immutable ON market_observations"
)

_DROP_IMMUTABLE_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS market_rows_are_immutable()")

sa.event.listen(
    market_providers,
    "after_create",
    _IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
sa.event.listen(
    market_providers,
    "after_create",
    _PROVIDERS_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    market_observations,
    "after_create",
    _OBSERVATIONS_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    market_providers,
    "before_drop",
    _DROP_PROVIDERS_TRIGGER.execute_if(dialect="postgresql"),
)
sa.event.listen(
    market_observations,
    "before_drop",
    _DROP_OBSERVATIONS_TRIGGER.execute_if(dialect="postgresql"),
)
# On `market_providers`, because it is dropped last of the two — the function is
# shared, and dropping it while the observations trigger still referenced it
# would leave the reversal half done.
sa.event.listen(
    market_providers,
    "before_drop",
    _DROP_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
