# `services/`

Python application services. Members of the `uv` workspace declared in the root
`pyproject.toml`.

These are **logical boundaries, not microservices** (spec §7). Do not split them
into separately deployed processes in V1 beyond the API/worker division that
background inference requires.
