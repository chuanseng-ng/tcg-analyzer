# `datasets/documentation`

Per-dataset documentation: source, licence, commercial-use rights, collection
method, known biases and limitations.

**Documentation only — never images.** The store itself is a schema domain in
PostgreSQL, not this directory — see
[ADR 0009](../../docs/adr/0009-the-dataset-store-as-a-database-domain.md).

## Contents

- [`contributor-photography-grant.md`](contributor-photography-grant.md) — the
  licence a collector signs before photographs they took enter the corpus.
  ADR 0008's approved source class 3, and the whole of what makes it usable.

**Approved source class 4 has no file here, and that is deliberate.** A user
consents to their own upload being kept on a screen rather than on paper, so the
document they agree to is the one the API serves — `CONSENT_TEXT` and
`CONSENT_VERSION` in
[`tcg_api/datasets/consent.py`](../../services/api/src/tcg_api/datasets/consent.py),
returned by `GET /training-consent` and recorded on every row as spec §29's
`license`. A copy of those words in this directory would be a second answer,
free to drift from the one a row says its grantor read. Editing them is editing
the version.
