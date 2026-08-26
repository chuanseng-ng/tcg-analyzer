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

## Expected value

`expected_value(distribution, prices, distribution_confidence=...)` is spec
§40's `EV = Σ P(g)·V(g)`. It takes a `tcg_domain.GradeDistribution` and a
`Mapping[Grade, GradedPrice]`, and returns an `ExpectedValue` or
`InsufficientInformation` — never a bare number and never a fabricated one.

| Field | What it is |
| --- | --- |
| `amount` | The expectation **conditional on a priced grade occurring** |
| `confidence` | `distribution_confidence * Σ P(g)·price_confidence(g)`, clamped to 1 |
| `unpriced_grades` | The grades excluded for want of a price, ascending |
| `unpriced_probability` | How much of the distribution they carried |

`distribution_confidence` has no default on purpose. Assuming `1.0` for a model
nobody measured is the same fabrication as pricing an unknown grade at zero,
pointed the other way.

### A missing `V(g)` is never zero

Spec §69/M5's acceptance criterion says so outright. Valuing an unpriced grade
at nothing drags the expectation below every price in the ladder and can flip a
recommendation from *grade* to *do_not_grade* on a card whose only unknown is
its best outcome. The grade is excluded, the rest renormalised, and the
exclusion reported in `unpriced_grades` / `unpriced_probability` so a caller can
say which outcomes went unvalued.

`insufficient_information` is returned only when no priced grade carries any
probability at all. "Too little of the distribution is priced to be worth
reporting" is a product judgement, and #64 owns it along with the rest of
`grade | do_not_grade | insufficient_information`.

A price of `0.00` is a price. `null` is not. That is the distinction
`GET /cards/{id}/market` already keeps on the wire.

### A bucket is worth the least it can be worth

Spec §24's own example distribution is
`{"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}`, and no market ladder
ever prices `7_or_lower` — the 55 pairs `GET /cards/{id}/market` serves are
exact grades. The rule:

1. A price under the bucket's **own key** wins. A caller who priced
   `7_or_lower` has answered the question.
2. Otherwise the bucket takes the **lowest price among the grades it covers** —
   for `7_or_lower`, the floor of what the ladder holds at or below 7; for
   `9_or_higher`, the floor of what it holds at or above 9. Only exact grades
   are scanned, so one bucket never prices another.
3. Covering nothing priced makes it unpriced, like any other grade.

The boundary grade's price was rejected: `7_or_lower` means "seven, or something
worse", so valuing it at `V(7)` prices the worst-case tail at its best-case
member — optimistic in the direction that tilts a recommendation toward *grade*.

The cost is that the floor is read off whichever grades the caller happened to
price, so a ladder missing its cheap end reports a higher floor for the same
bucket. The upgrade, when that stops being good enough, is for the caller to
price the bucket key itself — rule 1 already honours it — not for the engine to
learn the shape of a company's scale.

### The sum is exact and rounds once

Terms are `Decimal` products accumulated at full precision and turned into
`Money` at the end. `Money` quantises on construction, so multiplying per term
would round eighteen times: a distribution whose grades are all worth `100.04`
answers `100.05` that way, and `100.04` this way.

There is no selling fee here and no cost of any kind. ADR 0007 applies
`sale_costs` "per outcome, inside the sum, never to the expected value", which a
caller satisfies by netting each `V(g)` **before** handing the ladder over —
which is why this function grows no fee parameter and #60 owns cost subtraction.

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

## The incremental grading decision

`incremental_grading_decision(distribution, prices, raw_price, costs,
distribution_confidence=...)` is spec §41's first figure — *"I own this card; is
grading it worth it?"* — on ADR 0007's basis:

```python
graded_proceeds       = Σ P(g)·(V(g) − sale_costs(V(g)))
raw_opportunity_value = raw_market_value − sale_costs(raw_market_value)
incremental_profit    = graded_proceeds − raw_opportunity_value − grading_costs
```

`prices` are the **gross** graded values; the fee is netted off here, per
outcome. It returns an `IncrementalGradingDecision` or `InsufficientInformation`
— `no_graded_price_available` propagated from the expectation, or
`no_raw_price_available` when the raw side cannot be priced.

| Field | What it is |
| --- | --- |
| `incremental_profit` | ADR 0007's term. **Negative is a real answer** |
| `confidence` | `min(graded expectation, raw price)` |
| `graded_proceeds` | `Σ P(g)·(V(g) − fee)`, conditional on a priced grade |
| `raw_market_value` / `raw_selling_fee` / `raw_opportunity_value` | The raw branch, gross → fee → net |
| `grading_costs` | Five line items; the selling fee is not one |
| `costs` | The configuration it was computed against — already the per-line breakdown |
| `unpriced_grades` / `unpriced_probability` | Carried through from the expectation |

The breakdown is the point. A user shown a single number cannot tell whether the
recommendation turns on a shipping estimate or on the gap between the raw and
graded prices.

### The two omissions this figure exists to prevent

**Forgetting the raw-sale opportunity value.** The comparison is not "graded
proceeds against grading costs" — it is "graded proceeds against what the card
would fetch raw today". A card already worth 100 raw has to clear that bar
before grading has earned anything.

**Netting the selling fee off only one branch.** A raw sale pays it too, and the
two fees differ because the two prices do. On ADR 0007's example 2 the correct
answer is `84.00`; charging the fee only to the graded side reports `74.00`, and
comparing gross graded proceeds against a net raw value reports `110.00`. The
last is a silent, systematic bias toward *grade*.

### No acquisition cost, and no ROI

Spec §41 insists the incremental decision and the investment return be
implemented rather than conflated, so this function has **no parameter and no
field naming an acquisition cost** — there is no way to supply one, which is how
`test_no_acquisition_cost_can_reach_this_figure` checks it. `investment_return`
lives beside it in the same module and is where an acquisition cost goes, and
both ratios live in `roi.py` — see [The two ROIs](#the-two-rois) below.

### `min`, not a product

The figure is a *difference* between two quantities rather than one estimate
refined by another, so it is no better than its weakest side. Multiplying would
imply the two are independent evidence for one quantity and would compound a
third factor onto the two `expected_value` already multiplies. `min` is monotone
in both inputs, which is the property #64 needs.

### The fee is applied inside the sum

`incremental_grading_decision` nets `V(g) − fee.on(V(g))` into every rung of the
ladder and then calls `expected_value`, which is why that function grows no fee
parameter. `SellingFee.on` caps the fee at the sale price, so the two are not
interchangeable: a flat `100.00` fee on `V(9) = 50`, `V(10) = 300` evenly split
gives `100.00` inside the sum and `75.00` hoisted out. The cap also keeps every
netted price non-negative, so `GradedPrice` still accepts it, and keeps
`raw_opportunity_value` at or above zero — which is what makes ADR 0007's
"neither denominator can be negative" true before #62 ever divides by it.

## Investment return

`investment_return(distribution, prices, acquisition_cost, costs,
distribution_confidence=...)` is spec §41's second figure — *"I bought this card
to grade it; did it pay?"* — on ADR 0007's basis:

```python
graded_proceeds   = Σ P(g)·(V(g) − sale_costs(V(g)))
investment_profit = graded_proceeds − acquisition_cost − grading_costs
```

The raw market price is not an input. An investor's alternative was not buying
the card, not selling it ungraded, which is why this figure has no `raw_`-named
parameter or field and the incremental one has no acquisition cost. §41's "all
costs" is `grading_costs` plus the selling fee, and the fee is already netted out
of `graded_proceeds` — charging it again here would charge it twice.

| Field | What it is |
| --- | --- |
| `investment_profit` | ADR 0007's term. **Negative is a real answer** |
| `confidence` | The graded expectation's own, undiscounted |
| `graded_proceeds` | `Σ P(g)·(V(g) − fee)`, conditional on a priced grade |
| `acquisition_cost` | What the user says they paid. Always present on a result |
| `grading_costs` | Five line items; the selling fee is not one |
| `costs` | The configuration it was computed against |
| `unpriced_grades` / `unpriced_probability` | Carried through from the expectation |

### An absent acquisition cost is undefined, never zero

Spec §45 makes the acquisition cost optional user input and says outright: *do
not infer it*. So `None` returns `InsufficientInformation` with reason
`acquisition_cost_not_supplied` — ADR 0007's own string, which #65 puts on the
wire as `investment_roi_reason`.

Zero would report the whole of `graded_proceeds` less costs as profit on a card
the user may have paid dearly for. The raw market price — the other tempting
substitution — answers a question nobody asked: *"what if you had bought it at
today's price?"* The incremental figure is unaffected either way, because it
never sees this number. Same rule as #91's "Not measured" is never `0%`.

`0.00` **is** a real acquisition cost: a raffle win, or a pack from a box
somebody else paid for. It reports a figure, and `None` does not — the same
distinction `GET /cards/{id}/market` keeps between a price of `0.00` and `null`.

That is why `acquisition_cost` is typed `Money | None` where the incremental
figure's `raw_price` is `GradedPrice | None`. A market price is an *estimate* and
carries an age-discounted confidence; an acquisition cost is a fact the user
typed. A supplied one must be a non-negative `Money` — anything else raises
`InvalidAcquisitionCost`, and the non-negative half is what keeps
`CapitalAtRisk_inv = acquisition_cost + grading_costs` out of the negatives
before #62 divides by it.

### The confidence is the expectation's alone

No `min` here, because there is no second estimate to be no better than. The two
figures reaching different confidences on the same ladder is the point rather
than an inconsistency: the incremental one is additionally exposed to a raw price
that may be stale.

### One formula, two figures, no shared object

Both numerators are defined in terms of ADR 0007's `graded_proceeds`, so the
net-the-fee-then-sum step lives in a single private `_graded_proceeds` — a rule
this load-bearing written out twice is a rule that rots in one of the two places.
It is a shared *formula*, never a shared object: each call returns its own frozen
`ExpectedValue`, and the only object the two results have in common is the
caller's own frozen `CostConfiguration`.

Beyond that the two share no field name at all, which is what makes it impossible
for a caller to display one figure under the other's label — the conflation spec
§41 names specifically.

## The two ROIs

```python
incremental_roi(incremental_grading_decision(...))  -> Uncertain[IncrementalRoi]
investment_roi(investment_return(...))              -> Uncertain[InvestmentRoi]
```

```text
CapitalAtRisk_incr = raw_opportunity_value + grading_costs
incremental_roi    = incremental_profit / CapitalAtRisk_incr

CapitalAtRisk_inv  = acquisition_cost + grading_costs
investment_roi     = investment_profit / CapitalAtRisk_inv
```

Spec §42 says outright that *"the implementation must not casually choose a
denominator."* ADR 0007 chose both; `roi.py` carries them out and decides
nothing. All five of the ADR's worked examples are `tests/test_roi.py`'s
fixtures verbatim — regenerating them from the implementation would void
§69/M5's acceptance criterion.

### There are two, and there is never one

`incremental_roi` and `investment_roi` sit under §41's two profit figures, so a
denominator serving both would be the conflation §41 forbids — and a field
called `roi` alone would be the same mistake one layer down, since a caller
cannot label a number it cannot tell apart. `IncrementalRoi` and `InvestmentRoi`
share no field name. On ADR 0007's example 3 they report `0.5600` and `1.2286`
for the same card on the same day.

### The denominator includes the card

"Return on the money you spend to grade" is the number most tools report, and
ADR 0007 rejected it by name: the numerator has already subtracted the raw-sale
opportunity value, so a denominator omitting it pretends the card itself is not
committed. On example 1 the rejected basis reports `1.6667` where this one
reports `0.6250`. **The smaller number is the correct one** —
`test_the_rejected_costs_only_basis_is_not_what_we_report` is what fails if
somebody "fixes" it, and carrying that difference to a user comparing against a
competitor is real work for #66.

The selling fee is in neither denominator. It is paid out of proceeds rather
than committed up front, which is why `grading_costs` is five line items.

### Nothing recomputed, nothing re-decided

Both functions take the profit figure's `Uncertain` and read numerator and
denominator off it, so a ratio can never drift from the profit it is a ratio of,
and an unanswerable question is answered once where it arose:
`no_raw_price_available`, `no_graded_price_available` and
`acquisition_cost_not_supplied` all travel back out wearing their own reason.
An absent acquisition cost never becomes zero here because it never reaches
here.

### A zero denominator is an admission, not infinity

`InsufficientInformation("no_capital_at_risk")`, while the profit figure is
still reported. The guard is ADR 0007's zero and only its zero: `Money`
quantises to the cent so there is no near-zero band for a threshold to catch,
and an exact `Decimal` division cannot produce an unbounded figure the way a
float can. "Too small a base to report meaningfully" is #64's judgement about
the recommendation — inventing a floor here would amend an accepted ADR from
the implementation.

### Four places, and a label nothing can change

A ratio quantises to four places `ROUND_HALF_UP`, never to `Money`'s two, so
#65 can put `"0.5600"` on the wire as a string and never route a decision figure
through a binary float. The division runs in a `localcontext` at a fixed
precision, because `Decimal.__truediv__` otherwise reads whatever some unrelated
code left in the thread's context.

The labels are ADR 0007's — **"Return on grading"** and **"Return on your
investment"** — and each is a `ClassVar` rather than a field with a default: a
default can be overridden at construction, and the whole point is that nothing
can display `0.5600` under the investor's label.
