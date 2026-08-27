# `docs/`

Project documentation.

| Path | Contents |
| --- | --- |
| [`architecture.md`](architecture.md) | Domain architecture, the analysis pipeline, and the architectural invariants |
| [`analysis-pipeline.md`](analysis-pipeline.md) | What the image-quality gate, the card detector and normalization each do |
| [`api.md`](api.md) | The HTTP endpoints, what each one refuses, and why it refuses it that way |
| [`database.md`](database.md) | Migrations, the schema registry, the seeds, the catalog import, the test markers |
| [`development.md`](development.md) | Host-based workflows — the web application, object storage, background jobs |
| [`retention.md`](retention.md) | How long uploaded photographs and analyses are kept, and what deletes them |
| [`market-provider-research.md`](market-provider-research.md) | The rubric the V1 market-data provider is chosen against, and the evidence for each candidate |
| [`training-image-provenance-research.md`](training-image-provenance-research.md) | The rubric training-image sources are judged against, and what each one's licence actually permits |
| [`adr/`](adr) | Architecture decision records, one numbered file per decision |
| [`adr/template.md`](adr/template.md) | Starting point for a new ADR |

The root [`README.md`](../README.md) is the front page: what the product is, the
V1 scope, the prerequisites, and the one command that starts the whole stack.
Anything longer than that lives here. The working conventions live in
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

[`api.md`](api.md) does not restate the schema — the API documents itself at
`/openapi.json` and `/docs`, and a hand-written copy of the shapes would fall out
of date. What it carries is the part a schema cannot: what each endpoint refuses,
and why.
