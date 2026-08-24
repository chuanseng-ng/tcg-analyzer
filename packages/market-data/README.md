# `packages/market-data`

The `MarketDataProvider` port (spec §33) — `get_raw_price`,
`get_graded_price`, `get_price_history` — and an in-memory implementation of it.

Spec §2.4 forbids any price provider becoming a hard dependency of the core
domain, and ADR 0006 selects PokePriceTracker for V1 on that condition: the
vendor enters the system behind this interface and nowhere else. So there is no
`api_key`, `base_url`, SKU or HTTP type anywhere in the port, and importing
`tcg_market_data` pulls in nothing outside the standard library and this
project's own stdlib-only packages. `tests/test_market_data_purity.py` checks
that in a fresh interpreter rather than trusting it.

Depends on `tcg-domain` and `tcg-grading-companies` and nothing else.

## An absent price is a return value, not an exception and not a zero

Spec §38 requires the product to be able to say **market price unavailable**,
and §2.7 makes uncertainty a legitimate output. A price of **zero is a valid
observation** — a worthless card really is worth nothing — so a provider that
signalled absence with `Money.zero()` would feed a real number into
`EV = Σ P(g)·V(g)` and produce a confident, wrong recommendation.

Both price methods therefore return
`Uncertain[PriceObservation]`: an observation, or a falsy
`InsufficientInformation` carrying a reason. ADR 0006 is the case that forced
it — the V1 provider does not cover TAG at all, and the answer is
`insufficient_information`, **never a substituted PSA price and never an
interpolated one**.

A provider that *fails* is a different thing and raises
`MarketProviderUnavailable`. That maps to spec §66's `provider_error`, **not**
to `market_data_unavailable`; the two are deliberately distinct, and which of
them an absent price becomes depends on what the caller needed it for — a
judgement #55 and #56 make at the HTTP edge, not one this package can.

## Grade keys are checked against the company's scale

`PriceObservation` validates `(grading_company, grade)` on construction, so no
implementation can store a PSA 9.5 — a grade PSA does not issue, and one no
submission could ever return. BGS 9.5 is fine, and the difference falls out of
`tcg_grading_companies`' scales rather than a branch here.

A company with **no adapter** is accepted rather than refused. `GradingCompany`
is a vocabulary, not a closed enum, so that spec §22's "a fourth company costs
one new adapter and no caller change" stays true; refusing here would quietly
make `ADAPTERS` the closed set of valid companies.

## What is deliberately not here

- **No provider adapter** — #52, and it will live in its own module, not be
  re-exported from `__init__`.
- **No caching and no snapshots** — #51. `data_version` is a snapshot field
  (§36), not an observation one, so it is not on `PriceObservation`.
- **No `price_age` and no computed `price_confidence`** — #55. The observation
  carries the provider's own `confidence` in that one figure; staleness is a
  function of `observed_at` and the moment of asking.
- **No `Currency.USD`, and no conversion** — #53 owns normalization to SGD and
  is where a currency arrives alongside the rate that has to be recorded with
  it.
- **No `metadata`, and no licence fields.** `license`, `commercial_use` and
  `terms_reference` belong to #50's `market_providers` row, where they are
  recorded once. An observation carries the provider's lowercase **slug**; ADR
  0006's display name `PokePriceTracker` is that row's `name`.
