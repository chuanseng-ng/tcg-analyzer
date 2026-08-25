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

## Age is asked, never stored

`freshness.py` answers spec §38's two questions — `price_age(observation, at=)`
and `price_confidence(observation, at=, stale_after=)` — and both take the
moment of asking as an argument, because a stored age is wrong the second after
it is written. That is why `market_observations` has an `observed_at` and a
`confidence` and neither of these.

`confidence` and `price_confidence` are different numbers with similar names.
The first is what the provider thought that one figure was worth; the second is
that, discounted for how long ago it was true, and only the second is fit to
show a user. The discount is flat through `FRESH_WITHIN` (one ingestion cycle
— §37 refreshes daily, so a price a day old is the current one), then linear
to `STALE_FLOOR` of the provider's figure, and never below.

**The floor is above zero on purpose.** §38 forbids substituting stale data
*without identifying it*, not using it. A month-old price on a thinly traded
card is often the only evidence there is; reporting it at a fraction of its
original confidence says "old, and we know it", where reporting it at zero
would be indistinguishable from having nothing at all.

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
- **No caching, and no snapshot *resolution*.** `MarketSnapshot` — §36's four
  fields — lives here, beside `PriceObservation`, for the reason `GradingRules`
  lives in `packages/grading-companies`: it is a structure the specification
  names, so it belongs to the domain the specification gives it to. Generating
  and resolving one is `tcg_api.market.snapshots`, because a snapshot is a
  cut-line over rows in a database and there are no rows here. It is deliberately
  not part of the port either: a provider is asked for prices, never for a
  snapshot, since §37 forbids calling one on the read path at all. `data_version`
  stays a snapshot field, not an observation one, so it is not on
  `PriceObservation`.
- **No judgement about whether a confidence is good enough to act on** — M5.
  `freshness.py` stops at the number; deciding that too much is unavailable and
  the answer is `insufficient_information` needs the economic engine's inputs,
  and guessing them now would be a seam built against nothing.
- **No `Currency.USD`, and no conversion** — #53 owns normalization to SGD and
  is where a currency arrives alongside the rate that has to be recorded with
  it.
- **No `metadata`, and no licence fields.** `license`, `commercial_use` and
  `terms_reference` belong to #50's `market_providers` row, where they are
  recorded once. An observation carries the provider's lowercase **slug**; ADR
  0006's display name `PokePriceTracker` is that row's `name`.
