"""record a grading submission's outcome per copy

ADR 0008's primary approved source is *photographs we take of raw cards we own,
then submitted for grading*, and it is explicit about why: "the label is what
costs money, and that is the trade this decision makes". The label arrives weeks
after the photographs, and until this revision nothing in the schema had
anywhere to put it — `physical_copies` carries a certification and no grade,
`training_images` carries §29's nine provenance fields and no grade, and #158's
annotations are the *input* side of the supervised problem. Epic #9's acceptance
criterion is "±1 **actual grade**" and never says where the actual grade comes
from. This is where.

The shape and the reasoning live in
`services/api/src/tcg_api/datasets/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Six things worth knowing before reading the DDL:

* **One row per submission, never a column pair on `physical_copies`.** A copy
  can be graded by more than one company over its life, and ADR 0008's approved
  class 2 is a slab this project did not submit and whose outcome it still
  knows. A pair of columns would silently pick a winner between them.
* **`grade` is nullable and `designation` is its own column.** PSA issues
  "Authentic" *in place of* a numeric grade, so neither can be NOT NULL on its
  own — and widening `tcg_domain.Grade` to hold a designation would destroy the
  property that makes a grade usable as a distribution key and a database key.
  A submission carrying **neither** is not a submission, which is
  `outcome_is_a_grade_or_a_designation`.
* **The grade CHECK is the grade *grammar*, never a company's scale**, and it is
  narrower than `market_observations`': no `_or_lower` / `_or_higher`. A
  collapsed tail is what a model emits when it will not commit to one point; a
  slab prints one point. Which grades a company can issue is checked in Python,
  by `tcg_api.datasets.outcomes`, for the reason `market_observations` gives —
  a per-company CHECK would make a fourth company, or a scale revision, cost a
  migration of this table.
* **The four subgrades are recorded and nothing reads them.** V1 predicts an
  overall grade only (spec §24). BGS prints four, and an unrecorded subgrade
  cannot be recovered once the card is sold, so the cost of recording them is
  four nullable columns and the cost of not is permanent.
* **No `grading_rules_version` column.** Which published standard was in force
  is `rules_in_force(company, returned_at)` over `grading_rules` (#47); storing
  it would freeze today's reading, and a later re-read that finds a change with
  an earlier `effective_from` improves the derived answer while leaving a stored
  one wrong. Spec §57's reproducibility record is a different question and is
  M8's.
* **No immutability trigger.** An operator transcribes a grade and a
  certification number by hand off a slab, so a typo has to be correctable —
  the same argument `physical_copies` and `training_images` are mutable for, and
  ADR 0009 anticipates correcting records by script. `test_datasets_tables.py`
  and `test_datasets_schema.py` assert the absence, because it is a decision
  rather than an omission.

No rows are inserted. `downgrade()` drops the index and the table and **nothing
else**: this revision creates no function, and dropping
`dataset_records_are_immutable()` here would unguard `dataset_versions` and
`dataset_members`.

Alembic compares no triggers, so `compare_metadata` would not notice this and
`tables.py` drifting apart on one. Nothing here declares a trigger, and the
inverse-assertion tests are what keep that true.

Revision ID: b7e40d2a6c15
Revises: e5b8a3d47f21
Create Date: 2026-09-01 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e40d2a6c15"
down_revision: str | None = "e5b8a3d47f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# One point on a grade scale, as `tcg_domain.Grade` renders it. `10` is spelled
# out because `10.5` is not a grade and `[0-9](\.5)?` cannot say so. Written out
# as a literal rather than built from the table module: a migration is a
# snapshot of what was applied, and `test_datasets_tables.py` checks that the
# two still agree.
ISSUED_GRADE_PATTERN = r"^(10|[0-9](\.5)?)$"

# The four subgrades travel together or not at all — `image_annotations`'
# bounding-box idiom, which says so in one constraint rather than in four paired
# implications.
SUBGRADES_ARE_A_SET = (
    "num_nulls(subgrade_centering, subgrade_corners, subgrade_edges, subgrade_surface) IN (0, 4)"
)

# The same grammar as `grade`, over each subgrade. One constraint rather than
# four, so a failure names the rule rather than an arbitrary one of them.
SUBGRADES_ARE_GRADES = " AND ".join(
    f"({column} IS NULL OR {column} ~ '{ISSUED_GRADE_PATTERN}')"
    for column in (
        "subgrade_centering",
        "subgrade_corners",
        "subgrade_edges",
        "subgrade_surface",
    )
)


def upgrade() -> None:
    op.create_table(
        "grading_outcomes",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "physical_copy_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "The card this outcome is about. **CASCADE, where `training_images` restricts**: "
                "a copy cannot be deleted while any image references it, so by the time one can "
                "be removed this row describes nothing — and RESTRICT here would make a spec §54 "
                "disposal, or a contributor withdrawal, fail for a reason nobody chose. "
                "`training_image_fingerprints`' argument."
            ),
        ),
        sa.Column(
            "grading_company",
            sa.Text(),
            nullable=False,
            comment=(
                "Which company issued this outcome. NOT NULL and CHECKed against the vocabulary, "
                "unlike `grading_rules.company`: this is data *about* a company, the "
                "`market_observations.grading_company` precedent."
            ),
        ),
        sa.Column(
            "certification_number",
            PRINTED,
            nullable=False,
            comment=(
                "The number printed on the slab that came back. Written onto the "
                "`physical_copies` row as well, where that row carries none, so §32's grouping "
                "key and the slab agree — the write `physical_copies` was left mutable for."
            ),
        ),
        sa.Column(
            "grade",
            PRINTED,
            nullable=True,
            comment=(
                "What was issued, as `tcg_domain.Grade` renders it. NULL where a designation was "
                "issued **in place of** a numeric grade, which is exactly PSA Authentic. Which "
                "grades a company can issue is checked in Python, never here."
            ),
        ),
        sa.Column(
            "designation",
            sa.Text(),
            nullable=True,
            comment=(
                "A label that is not a point on the scale — PSA Authentic, BGS Black Label, TAG "
                "Pristine 10. Its own column rather than a sixth value on the scale, which is "
                "what `tcg_grading_companies.companies`' ponytail note anticipated. BGS Black "
                "Label accompanies grade 10; PSA Authentic replaces a grade."
            ),
        ),
        sa.Column(
            "subgrade_centering",
            PRINTED,
            nullable=True,
            comment=(
                "BGS's centering subgrade, where the slab prints one. Recorded rather than read: "
                "V1 predicts an overall grade only (§24), and an unrecorded subgrade cannot be "
                "recovered once the card is sold. All four or none."
            ),
        ),
        sa.Column(
            "subgrade_corners",
            PRINTED,
            nullable=True,
            comment=(
                "BGS's corners subgrade, where the slab prints one. Recorded rather than read: V1 "
                "predicts an overall grade only (§24), and an unrecorded subgrade cannot be "
                "recovered once the card is sold. All four or none."
            ),
        ),
        sa.Column(
            "subgrade_edges",
            PRINTED,
            nullable=True,
            comment=(
                "BGS's edges subgrade, where the slab prints one. Recorded rather than read: V1 "
                "predicts an overall grade only (§24), and an unrecorded subgrade cannot be "
                "recovered once the card is sold. All four or none."
            ),
        ),
        sa.Column(
            "subgrade_surface",
            PRINTED,
            nullable=True,
            comment=(
                "BGS's surface subgrade, where the slab prints one. Recorded rather than read: V1 "
                "predicts an overall grade only (§24), and an unrecorded subgrade cannot be "
                "recovered once the card is sold. All four or none."
            ),
        ),
        sa.Column(
            "returned_at",
            sa.Date(),
            nullable=True,
            comment=(
                "When the slab came back. **NULL is meaningful**: ADR 0008's approved class 2 is "
                "a slab this project did not submit and whose outcome it still knows, and there "
                "is no return date to invent for one."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(certification_number) <> ''", name="certification_number_is_not_blank"
        ),
        sa.CheckConstraint(
            "designation IS NULL OR designation IN "
            "('authentic', 'authentic_altered', 'black_label', 'pristine_10', 'gem_mint_10')",
            name="designation_is_a_known_designation",
        ),
        sa.CheckConstraint(
            f"grade IS NULL OR grade ~ '{ISSUED_GRADE_PATTERN}'", name="grade_is_an_issued_grade"
        ),
        sa.CheckConstraint(
            "grading_company IN ('psa', 'tag', 'bgs')", name="grading_company_is_supported"
        ),
        sa.CheckConstraint(
            "grade IS NOT NULL OR designation IS NOT NULL",
            name="outcome_is_a_grade_or_a_designation",
        ),
        sa.CheckConstraint(SUBGRADES_ARE_A_SET, name="subgrades_are_four_or_none"),
        sa.CheckConstraint(SUBGRADES_ARE_GRADES, name="subgrades_are_issued_grades"),
        sa.ForeignKeyConstraint(
            ["physical_copy_id"],
            ["physical_copies.id"],
            name="fk_grading_outcomes_physical_copy_id_physical_copies",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grading_outcomes"),
        sa.UniqueConstraint(
            "grading_company", "certification_number", name="uq_grading_outcomes_certification"
        ),
        comment=(
            "**One grading submission's outcome** — what one company issued for one physical "
            "card, once. Epic #9's acceptance criterion is ±1 *actual grade* and never says where "
            "the actual grade comes from; this is where. **One row per submission, never a column "
            "pair on `physical_copies`**: a copy can be graded by more than one company over its "
            "life, and a column pair would silently pick a winner. Deliberately not write-once, "
            "and it carries no grading rules version — see the comments above."
        ),
    )
    op.create_index(
        "ix_grading_outcomes_physical_copy_id", "grading_outcomes", ["physical_copy_id"]
    )


def downgrade() -> None:
    # The index and the table, and nothing else. This revision creates no
    # trigger function, and `dataset_records_are_immutable()` still guards
    # `dataset_versions` and `dataset_members` — dropping it here would silently
    # unguard both.
    op.drop_index("ix_grading_outcomes_physical_copy_id", table_name="grading_outcomes")
    op.drop_table("grading_outcomes")
