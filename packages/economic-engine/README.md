# `packages/economic-engine`

Expected value over a grade distribution, the incremental grading decision,
investment return, ROI and the optimization strategies.

**Grading is separate from economics.** This package answers "is grading worth
it?" and must never depend on how a grade was predicted. It depends on
`tcg-domain` and nothing else, and `tests/test_economic_engine_purity.py`
enforces that with a real import in a fresh interpreter. Every formula is
unit-tested against hand-calculated fixtures.

The *incremental grading decision* and *investment return* are distinct figures
and must not be conflated. `docs/adr/0007-roi-and-the-capital-at-risk-basis.md`
is the authority on what each one means.

```bash
uv run pytest packages/economic-engine
```

## Cost configuration

`CostConfiguration` is spec §46's six line items, all in SGD, all overridable,
and — because §47 adds country, currency, service tier, declared value, shipping
provider and tax later — each one its own named field. Nothing in this package
computes a grand total: a §47 dimension attaches to a single line, so collapsing
the lines would make every one of those a rewrite rather than an addition.

| Field | Default | What it is |
| --- | --- | --- |
| `grading_fee` | `40.00` | The company's charge per card for the chosen tier |
| `outbound_shipping` | `30.00` | Getting the card to the grader |
| `return_shipping` | `30.00` | Getting it back |
| `insurance` | `0.00` | Cover for the round trip |
| `miscellaneous` | `0.00` | Sleeves, semi-rigids, a courier surcharge |
| `selling_fee` | 10% + `0.00` | What selling costs, once |

**The defaults are illustrative placeholders, not quoted rates.** §46 rules out
a regional pricing system in V1 and this package fetches nothing from a grading
company's site. They are non-zero because an all-zero default would report
grading as costless and tilt every recommendation toward *grade* — the same
failure ADR 0007 rejects for a zeroed acquisition cost. Replace them with what
the user was actually quoted.

A configuration is frozen: §57's reproducibility record names the configuration
an analysis was computed against, so re-running with different costs is a new
analysis rather than an edit. Override with `dataclasses.replace`.

### `grading_costs` is five line items, not six

```python
grading_costs = grading_fee + outbound_shipping + return_shipping
              + insurance + miscellaneous
```

The selling fee is deliberately absent. ADR 0007 puts it in proceeds rather than
in `CapitalAtRisk`, because it is paid out of a sale that may not happen rather
than committed up front — and both ROI denominators are built on this sum, so
"completing" it to §46's six items breaks both ratios.

### The selling fee

§46 fixes the field but not its shape, and ADR 0007 leaves the shape to this
package. `SellingFee` carries a `rate` (a proportion of the realised sale price,
in `[0, 1]`) and a `flat` amount; either may be zero, and `fee.on(price)` charges
their sum **capped at the sale price**.

The cap is not defensive rounding. ADR 0007 states that neither `CapitalAtRisk`
denominator can be negative "because both are sums of non-negative quantities",
and `raw_opportunity_value` is the raw market value less the fee on it — so an
uncapped flat fee on a cheap card would falsify that claim. It makes the fee
piecewise-affine rather than affine, which is precisely the case ADR 0007's
apply-the-fee-inside-the-sum rule exists for.

Acquisition cost is **not** part of this model: spec §45 makes it optional user
input that must never be inferred, and ADR 0007 reports the investment figures as
`null` when it is absent.
