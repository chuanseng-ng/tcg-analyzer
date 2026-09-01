# `docs/adr`

Architecture decision records.

One numbered file per decision, recording context, the decision and its
consequences. Decisions the spec left open are settled here so they are not
re-litigated silently later.

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-language-boundaries-in-the-monorepo.md) | Language boundaries in the monorepo | accepted | 2026-08-16 |
| [0002](0002-object-storage-behind-a-port.md) | Object storage behind a port | accepted | 2026-08-17 |
| [0003](0003-the-local-development-stack.md) | The local development stack | accepted | 2026-08-18 |
| [0004](0004-the-canonical-card-catalog-source.md) | The canonical card catalog source | accepted | 2026-08-18 |
| [0005](0005-rate-limiting-the-analysis-endpoints.md) | Rate limiting the analysis endpoints | accepted | 2026-08-20 |
| [0006](0006-the-v1-market-data-provider.md) | The V1 market-data provider | accepted | 2026-08-24 |
| [0007](0007-roi-and-the-capital-at-risk-basis.md) | ROI and the CapitalAtRisk basis | accepted | 2026-08-25 |
| [0008](0008-permitted-training-image-sources.md) | Permitted training-image sources | accepted | 2026-08-28 |
| [0009](0009-the-dataset-store-as-a-database-domain.md) | The dataset store as a database domain | accepted | 2026-08-28 |
| [0010](0010-what-surface-defects-are-measured-against.md) | What surface defects are measured against | accepted | 2026-08-29 |
| [0011](0011-the-v1-grade-predictor-basis.md) | The V1 grade predictor is a declared-uncertainty baseline | accepted | 2026-09-02 |

[`template.md`](template.md) is the shape for a new one. An accepted ADR is not
rewritten: a decision that changes gets a new record, and the old one is marked
superseded here and in its own front matter.
