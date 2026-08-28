# `datasets/`

Dataset **schemas, manifests and documentation only — never images.**

**The store itself is a database domain, not this directory.** Dataset,
provenance and annotation records live in PostgreSQL beside the catalog,
analysis, grading, market and economics domains, which is where ADR 0008's
commercial-use gate is enforced as a constraint rather than as a convention —
see [ADR 0009](../docs/adr/0009-the-dataset-store-as-a-database-domain.md). What
this directory holds is the human-readable half: the shapes described in
`schemas/`, an immutable manifest **generated** per dataset version in
`manifests/`, and the per-dataset prose in `documentation/`. Image bytes live in
object storage.

Provenance gates training data: every training image needs a documented source,
licence and commercial-use rights, and the training pipeline rejects images
whose commercial-use status is unknown. Public accessibility is not permission.
Which sources qualify is settled by
[ADR 0008](../docs/adr/0008-permitted-training-image-sources.md).

Dataset versions are immutable — a model records the exact dataset version it
trained on. Nothing here is ever published: `redistribution_allowed` is `false`
on every approved source, so a manifest of identifiers and content hashes is the
most a dataset version can leave behind.

Populated in M6.
