# `datasets/schemas`

Schema definitions for datasets, annotations and provenance records, **as
documentation for a human**. The tables themselves are a database domain and
the DDL is an Alembic migration — see
[ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md). A
file here that disagrees with a migration is stale documentation, never a second
definition.

- [The dataset, provenance and membership schema](dataset-schema.md) —
  `physical_copies`, `training_images`, `training_image_fingerprints`,
  `dataset_versions` and `dataset_members`, and how the per-copy identifier is
  derived for each of ADR 0008's four approved sources.
- [The annotation schema](annotation-schema.md) — `image_annotations` and
  `centering_measurements`, where each of spec §30's eleven features lives, and
  why a defect marker and a centering measurement are two tables rather than one.

**Schemas only — never images.**
