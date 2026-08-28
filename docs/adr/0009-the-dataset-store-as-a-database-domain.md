# ADR 0009 — The dataset store as a database domain

- **Status:** accepted
- **Date:** 2026-08-28
- **Refs:** M6, #152, spec §7, §29, §30, §31, §32, §55, §77

## Context

[ADR 0008](0008-permitted-training-image-sources.md) decided **what** may be
stored. Nothing has decided **where**, and the next issue cannot be written
until it is: the dataset schema adds the per-copy identifier §32 groups on, the
training images carrying §29's nine provenance fields, the immutable dataset
versions §31 requires and the membership that assigns each image to a split —
and ADR 0008's commercial-use gate has to be enforced somewhere concrete.

The question would be unremarkable if this repository were silent on it. It is
not; it currently says the opposite. `datasets/README.md` opens with *"Dataset
**schemas, manifests and documentation only — never images**"*, and
`datasets/schemas/README.md` reads *"Schema definitions for datasets,
annotations and provenance records."* Spec §7 puts
`datasets/{schemas,manifests,documentation}` in the tree beside
`database/{migrations,seeds,fixtures}` and says nothing about where a provenance
record lives. A contributor deciding where a new dataset table goes reads that
first and concludes the directory is the store.

### The file-based store, on its merits

The option genuinely on the table was files: the images in object storage, a
JSON or YAML sidecar per image carrying §29's nine fields, and a manifest per
dataset version listing members and splits. It is the conventional machine
learning layout, it is what §7's tree implies, and its advantages are real.
Provenance is readable and diffable in a text editor. A training run needs a
filesystem and nothing else — no database, no migration, no service. The
manifest *is* the store rather than a render of it, so a version cannot disagree
with the file that describes it. For a project whose `ml/*` packages are
deliberately dependency-light, it is the layout with the fewest moving parts.

Five things are wrong with it.

**ADR 0008's gate stops being a constraint and becomes a function somebody has
to remember to call.** The rule is that a null, an empty string and an absent
field are one answer and that answer is refusal. An absent field is exactly what
a hand-maintained sidecar produces, and the failure is silent: an image whose
`commercial_use_allowed` key was never written trains anyway unless a loader
happens to check. This is the one gate in the project whose failure mode is a
licensing breach rather than a bug, and §29 states it as a property of *every*
image rather than of a code path.

**Nothing relates an image to anything else.** A training image references a
physical copy, and — for the identification work — a `cards` row that already
lives in PostgreSQL. Across sidecars those are strings. Nothing stops a
manifest naming an image that was deleted, a provenance record outliving its
image, or two records claiming the same photograph.

**§31's immutability becomes a promise.** A dataset version would be a file
anybody can edit. This repository does not enforce immutability by promise
anywhere else, and deliberately: `card_database_versions` (#27) and the market
tables (#50) refuse an `UPDATE` in a trigger.

**§32's anti-leakage rule and deduplication both become directory scans.**
Grouping by physical copy, by source or by certification is a query over
columns; over sidecars it is a scan that rebuilds an index on every run.
Deduplicating on `sha256` is the same scan again.

**It stands up a second store beside one already running.** The card a training
image depicts is a row in PostgreSQL. A file-based provenance store means the
join between the two is written in Python, by hand, in every consumer.

**The decisive argument is the repository's own habit: the rule goes in the
schema.** Immutability is a trigger (#27, #50); `market_type` is a generated
column rather than a value a caller may contradict (#50); §65's state machine is
the `WHERE` clause of a conditional `UPDATE`; a market snapshot's `data_version`
is generated from the cut-line rather than chosen (#51). A gate enforced in
Python, beside five domains that enforce their rules in PostgreSQL, would be the
odd one out — and it would be the odd one out in the place it matters most.

A hybrid — rows for membership, sidecars for provenance — was considered and
rejected in one line: it leaves the gate outside the database, which is the
entire reason for choosing the database.

## Decision

**The dataset, provenance and annotation store is a sixth schema domain in
PostgreSQL.** Its tables are declared in
`services/api/src/tcg_api/datasets/tables.py` and registered in
`services/api/src/tcg_api/table_registry.py` — the one place a domain is
registered — and migrated with Alembic like every other domain. It sits beside
catalog, analysis, grading, market and economics and is bound by the same rules:
a table is declared in its domain module as well as in its migration, and a
domain the registry does not import is a domain `alembic revision
--autogenerate` proposes dropping.

**§29's nine provenance fields are columns on the image row, and ADR 0008's gate
is a constraint over them.** The shape is the schema issue's to fix; what this
record fixes is that the gate lives in the schema and not in a caller.

**`datasets/` is not the store.** What it holds is stated here so that directory
and this record cannot drift apart:

- **`datasets/schemas/`** — the shapes described for a human. The DDL is the
  migration. A file here that disagrees with a migration is stale documentation,
  never a second definition.
- **`datasets/manifests/`** — an immutable manifest **generated** per dataset
  version from the membership rows: image identifiers, content hashes and split
  assignment. Generated, never hand-written. It is what makes a training run
  reproducible **without publishing an image**, which ADR 0008 requires:
  `redistribution_allowed` is `false` on every approved source, including the
  photographs this project took itself, so a list of identifiers and hashes is
  the most that can ever leave the database.
- **`datasets/documentation/`** — per-dataset prose: source, licence,
  commercial-use rights, collection method, known biases and limitations, and
  the contributor grant template ADR 0008 requires.
- **Never images.** That line stands unchanged, and
  `tests/test_repository_structure.py` enforces it against git's index.

**Image bytes live in object storage**, behind `packages/shared`'s
`ObjectStorage` port ([ADR 0002](0002-object-storage-behind-a-port.md)), as
uploaded photographs already do. A row carries the storage key and the `sha256`;
the bytes are never a column and never in git.

**`ml/*` stays pure and reads a manifest, not the database.** No `ml/*` package
declares a database driver or an object-storage client, and this decision adds
none: `services/api` owns persistence for this domain as it does for every
other. A training run consumes a generated manifest, which is also what makes
§31's *"every training run must reference `dataset_version`"* checkable — the
manifest is the version, rendered.

**Nothing in this domain is on the public API.** §64's endpoints are the
consumer product; the dataset and annotation surfaces are internal.

**The annotation tool's isolation is a deployment question, and is named as one
rather than answered with a second service.** `apps/annotation/README.md`
requires that the tool *"must never be exposed with the consumer application"*.
That is satisfied by how the surface is deployed and reached — a separate
ingress, not routable from the public origin — and §55's rules apply to it
unchanged. §7 says outright not to create unnecessary microservices in V1, and a
second FastAPI application would duplicate the session, error-envelope and
migration wiring in order to enforce a boundary the deployment already enforces.
Two things would reopen this: annotation traffic that cannot be separated at the
edge, or a second party annotating. Either is a new ADR.

## Consequences

**What this makes easy.**

- The commercial-use gate is a constraint, so an image that fails it cannot be
  inserted at all. ADR 0008's *"unknown is `false`"* is enforced rather than
  observed.
- Deduplication is a unique index on `sha256`: the same photograph cannot enter
  a corpus twice under two different provenance records.
- §32's grouping — physical copy, source, certification — is a query over
  columns, so a leaking split is a bug that can be tested for rather than a
  property of whichever script last walked the directory.
- §31's immutability is a trigger, and a published dataset version cannot be
  edited after a model has recorded it.
- An image, its provenance, its physical copy and the card it depicts are one
  join, and referential integrity is what keeps them related.
- One store to migrate, to back up, and to reason about retention in.

**What this makes expensive.**

- Training gains an export step. A manifest has to be generated before a run, and
  the database has to be running to generate it. The file-based option needed
  neither.
- The annotation tool talks to an API rather than to a filesystem, so annotating
  requires the stack up. Correcting a provenance record by hand is a script, not
  an editor.
- Every change to the dataset shape is a migration and a review — including the
  exploratory ones during M6 and M7, which is when the shape is least settled and
  the cost is felt most.
- The internal domain shares a database, and therefore a connection pool, with
  the request path. That is a real operational risk; the decision names it as a
  deployment concern rather than pretending it away.

**What this forecloses.**

- A dataset committed to this repository, in any form other than a manifest of
  identifiers and hashes. ADR 0008 permits no publication, and this is the shape
  that survives it.
- A file-first store. Returning to one is a new ADR, and by then it costs an
  exporter and a re-implementation of the gate.
- A separate database for the dataset domain, for the same reason a second
  service is foreclosed: V1 runs one PostgreSQL, and a second is a deployment
  decision nobody has needed yet.

---

This record says where the store lives. It says nothing about what may enter it:
ADR 0008 governs that, and this decision must not be cited as widening it.
