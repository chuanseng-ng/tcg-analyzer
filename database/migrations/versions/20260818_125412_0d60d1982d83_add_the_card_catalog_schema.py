"""add the card catalog schema

Spec §10's three tables — `sets`, `cards`, `card_external_ids` — the canonical
catalog every other domain references. The shape and the reasoning live in
`services/api/src/tcg_api/catalog/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from it,
because a migration is a snapshot of what was applied and must not change when
that module does.

Three things worth knowing before reading the DDL:

* **`cards` carries no provider column.** Several external databases may point at
  one canonical card, so provider identifiers live only in `card_external_ids`.
  A provider column here would make one source authoritative (spec §2.4).
* **Nothing hard-codes Pokémon.** `game` is a column, so One Piece, Yu-Gi-Oh! and
  Magic are rows of data rather than a future migration (spec §73).
* **Plain PostgreSQL only.** No extensions, nothing Supabase-specific. That is
  also why substring name search is left to a sequential scan: `pg_trgm` would
  index it, and taking that step is a deliberate, measured decision rather than
  something to smuggle into the first catalog migration.

This is the migration that introduces the first domain table, so it is also the
one that drops `migration_harness_check` — the baseline revision said so in its
own `COMMENT ON TABLE`, and `database/migrations/README.md` said so in prose.
`downgrade()` puts it back, comment and all, so the history reverses exactly.

Revision ID: 0d60d1982d83
Revises: 0255d9f37125
Create Date: 2026-08-18 12:54:12.281091+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0d60d1982d83"
down_revision: str | None = "0255d9f37125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text — set codes, card numbers, names — ordered by byte rather than by
# whatever locale the server happened to be initialised with. The local Compose
# database runs initdb with `--locale=C`; CI's PostgreSQL service inherits the
# image default. Without this the two would disagree on index ordering and on
# whether a `LIKE 'x%'` index is usable at all, which is exactly the sort of
# difference that passes locally and fails in CI. Japanese is unaffected: it has
# neither case nor locale-dependent folding.
PRINTED = sa.Text(collation="C")

# IMMUTABLE, as PostgreSQL requires of a stored generated column: everything
# before the first "/", without leading zeros. "025/165" -> "25", so a user who
# types what they read off the card ("25") matches what is printed on it.
CARD_NUMBER_KEY_EXPRESSION = "ltrim(split_part(card_number, '/', 1), '0')"

NO_METADATA = sa.text("'{}'::jsonb")

HARNESS_TABLE = "migration_harness_check"

HARNESS_TABLE_COMMENT = (
    "Scaffolding, not domain. This table exists only to prove the Alembic "
    "migration harness applies and reverts cleanly end to end (M0, #15). It "
    "carries no data and will be dropped by the migration that introduces the "
    "first domain table."
)


def upgrade() -> None:
    op.create_table(
        "sets",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Ours, not a provider's. A provider's identifier is a card_external_ids row.",
        ),
        sa.Column(
            "game",
            sa.Text(),
            nullable=False,
            comment=(
                "A lowercase slug: 'pokemon' in V1. A column and never a constant — "
                "spec §73 requires One Piece, Yu-Gi-Oh! and Magic be rows of data, "
                "not a migration."
            ),
        ),
        sa.Column(
            "language",
            sa.Text(),
            nullable=False,
            comment=(
                "An ISO 639-1 code. Part of the set's identity: Japanese sets are "
                "distinct sets with their own numbering, not translations."
            ),
        ),
        sa.Column(
            "set_code",
            PRINTED,
            nullable=False,
            comment=(
                "The publisher's set identifier, verbatim — 'SV3a'. Absent from §10's "
                "column list; the domain entity requires it."
            ),
        ),
        sa.Column(
            "name",
            PRINTED,
            nullable=False,
            comment="The set's printed name, in its own language.",
        ),
        sa.Column(
            "release_date",
            sa.Date(),
            nullable=True,
            comment="A calendar date, never a timestamp: the day the set went on sale.",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=NO_METADATA,
            nullable=False,
            comment="Whatever the source carried that has no column of its own.",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sets"),
        sa.UniqueConstraint("game", "language", "set_code", name="uq_sets_game_language_set_code"),
        # Redundant on its own — `id` is already unique — and present only so
        # that `cards` can point a composite foreign key at it. PostgreSQL
        # requires a unique constraint covering exactly the referenced columns.
        sa.UniqueConstraint("id", "game", "language", name="uq_sets_id_game_language"),
        comment="One published set. Provider-independent, per spec §10.",
    )

    op.create_table(
        "cards",
        sa.Column("id", sa.Uuid(), nullable=False, comment="Ours, not a provider's."),
        sa.Column(
            "game",
            sa.Text(),
            nullable=False,
            comment=(
                "Denormalised from the set and held there by fk_cards_set_id_game_language_sets."
            ),
        ),
        sa.Column(
            "language",
            sa.Text(),
            nullable=False,
            comment=(
                "Denormalised from the set and held there by fk_cards_set_id_game_language_sets."
            ),
        ),
        sa.Column("set_id", sa.Uuid(), nullable=False),
        sa.Column(
            "card_number",
            PRINTED,
            nullable=False,
            comment=(
                "The number printed on the card, verbatim — '025/165'. Never normalised in place."
            ),
        ),
        sa.Column(
            "card_number_key",
            PRINTED,
            sa.Computed(CARD_NUMBER_KEY_EXPRESSION, persisted=True),
            nullable=False,
            comment=(
                "card_number reduced to its numerator without leading zeros, so that "
                "a user typing '25' matches a card printed '025/165'. Generated; "
                "never written."
            ),
        ),
        sa.Column(
            "name",
            PRINTED,
            nullable=False,
            comment="The card's printed name, in its own language.",
        ),
        sa.Column(
            "rarity",
            PRINTED,
            nullable=True,
            comment="The printed rarity, where the source records one.",
        ),
        sa.Column(
            "variant",
            PRINTED,
            nullable=True,
            comment=(
                "The printing variant — '1st-edition-holo'. Economically "
                "load-bearing: holo, reverse holo and 1st edition trade at very "
                "different prices, so this is never inferred and never dropped."
            ),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=NO_METADATA,
            nullable=False,
            comment="Whatever the source carried that has no column of its own.",
        ),
        sa.Column(
            "image_front",
            sa.Text(),
            nullable=True,
            comment=(
                "Stays NULL in V1. ADR 0004: TCGdex's MIT licence covers its "
                "compilation, not The Pokémon Company's artwork, so the only card "
                "images this product shows are the user's own uploads."
            ),
        ),
        sa.Column(
            "image_back",
            sa.Text(),
            nullable=True,
            comment="Stays NULL in V1, on the same terms as image_front. See ADR 0004.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cards"),
        # The card's game and language are its set's, and this is what makes
        # that true rather than merely intended. RESTRICT because deleting a set
        # out from under its cards is never what someone meant.
        sa.ForeignKeyConstraint(
            ["set_id", "game", "language"],
            ["sets.id", "sets.game", "sets.language"],
            name="fk_cards_set_id_game_language_sets",
            ondelete="RESTRICT",
        ),
        # NULLS NOT DISTINCT because `variant` is nullable, and PostgreSQL's
        # default treatment of NULL would let the same variant-less card be
        # inserted twice.
        sa.UniqueConstraint(
            "set_id",
            "card_number",
            "variant",
            name="uq_cards_set_id_card_number_variant",
            postgresql_nulls_not_distinct=True,
        ),
        comment="One canonical card. No provider column, by design — see card_external_ids.",
    )
    op.create_index("ix_cards_set_id", "cards", ["set_id"])
    op.create_index(
        "ix_cards_game_language_card_number_key",
        "cards",
        ["game", "language", "card_number_key"],
    )
    # Serves equality and prefix on the name. A *substring* search — which is
    # what CardQuery.text is — cannot use a B-tree and falls back to a sequential
    # scan. Deliberate at V1 catalog scale: pg_trgm would index it, and this
    # repository's migrations stay extension-free so the deployment platform
    # stays open between ordinary PostgreSQL and Supabase (spec §8).
    op.create_index("ix_cards_game_language_name", "cards", ["game", "language", "name"])

    op.create_table(
        "card_external_ids",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="A lowercase slug naming the source — 'manual' or 'tcgdex' in V1 (ADR 0004).",
        ),
        sa.Column(
            "external_id",
            PRINTED,
            nullable=False,
            comment="The identifier as that provider issued it, verbatim.",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=NO_METADATA,
            nullable=False,
            comment="Whatever the source carried that has no column of its own.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_card_external_ids"),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name="fk_card_external_ids_card_id_cards",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "card_id",
            "provider",
            "external_id",
            name="uq_card_external_ids_card_id_provider_external_id",
        ),
        comment=(
            "Spec §10: several external databases may reference the same canonical "
            "card. This table is also the seam that keeps them replaceable — dropping "
            "a source costs a DELETE here and nothing else."
        ),
    )
    # Deliberately not unique. A provider that does not split printing variants
    # issues one identifier for what we hold as several cards, so one
    # (provider, external_id) may legitimately name our holo row and our reverse
    # holo row both. Asserting uniqueness would make importing such a source a
    # schema change.
    op.create_index(
        "ix_card_external_ids_provider_external_id",
        "card_external_ids",
        ["provider", "external_id"],
    )

    # The first domain tables now exist, so the baseline's scaffolding has done
    # its job. It said as much in its own COMMENT ON TABLE.
    op.drop_table(HARNESS_TABLE)


def downgrade() -> None:
    op.create_table(
        HARNESS_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        comment=HARNESS_TABLE_COMMENT,
    )

    op.drop_index("ix_card_external_ids_provider_external_id", table_name="card_external_ids")
    op.drop_table("card_external_ids")

    op.drop_index("ix_cards_game_language_name", table_name="cards")
    op.drop_index("ix_cards_game_language_card_number_key", table_name="cards")
    op.drop_index("ix_cards_set_id", table_name="cards")
    op.drop_table("cards")

    op.drop_table("sets")
