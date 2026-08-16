# `services/api`

The FastAPI HTTP surface — the only component the web application talks to.

Owns request validation, the error taxonomy, OpenAPI generation and the
reproducibility record attached to every analysis. It rejects model output whose
grade distribution is invalid (spec §63).

Bootstrapped in M0 (#13).
