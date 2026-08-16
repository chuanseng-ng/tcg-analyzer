# `packages/`

Shared Python libraries — the domain core and the replaceable-provider ports.

Python rather than TypeScript because `services/api` and every `ml/` module must
import them; see `docs/adr/0001-language-boundaries-in-the-monorepo.md`.

Members of the `uv` workspace declared in the root `pyproject.toml`.
