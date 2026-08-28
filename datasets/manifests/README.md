# `datasets/manifests`

Immutable, versioned manifests listing the images in each dataset version by
identifier and content hash, together with their train/validation/test split.
**Generated** from the dataset domain in the database, never hand-written — see
[ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md).

**Manifests only — never images.**
