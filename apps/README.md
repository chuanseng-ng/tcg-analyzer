# `apps/`

User-facing applications. TypeScript, managed by the pnpm workspace declared in
`pnpm-workspace.yaml`.

Applications consume the API over HTTP. They never import a Python package
directly and never talk to the database.
