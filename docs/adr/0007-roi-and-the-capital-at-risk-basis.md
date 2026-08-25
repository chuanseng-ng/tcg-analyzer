# ADR 0007 — ROI and the CapitalAtRisk basis

- **Status:** accepted
- **Date:** 2026-08-25
- **Refs:** M5, #57, spec §39, §41, §42, §45, §78

## Context

Spec §42 requires that ROI be defined in the domain documentation before it
reaches the UI, and says outright that *"the implementation must not casually
choose a denominator."* Spec §78 carries it as an unresolved decision. Nothing
in this repository computes ROI, so the definition is still free — and that is
the only moment it is free, because §41's two profit figures are already fixed
and the denominator has to agree with whichever numerator it sits under.

§41 requires two distinct profit figures and forbids conflating them:

- the **incremental grading decision** — expected graded proceeds, less the
  raw-sale opportunity value, less incremental grading costs. This is the
  question a collector who already owns the card is asking.
- the **investment return** — expected final proceeds, less acquisition cost,
  less all costs. This is the question an investor who bought the card to grade
  is asking.

Three denominators for the incremental figure were genuinely on the table.

**Incremental costs only** — "return on the money you spend to grade." It is
the easiest number to explain and the one most tools report. It is also
internally inconsistent: the numerator subtracts the raw-sale opportunity value,
so the denominator would be pretending the card itself is not committed. On the
worked example below it reports 166.7% where the consistent basis reports 62.5%,
and the gap is not conservatism — it is the raw value going missing from one
side of a ratio and not the other.

**One shared basis for both figures** — `acquisition_cost + all costs`, used
under both numerators. Simplest to implement, and wrong twice: it makes the
incremental ROI undefined whenever acquisition cost is absent, which §45 makes
the ordinary case, and sharing a denominator is precisely the conflation §41
forbids.

**Raw-sale opportunity value plus incremental costs** — the numerator and the
denominator agree on what has been committed. This is what was chosen.

A second question had to be answered with it: what ROI reports when the user
does not supply an acquisition cost. §45 says the value is `null` and must not
be inferred, but says nothing about what the figures derived from it become.
Zero is the answer that writes itself and is a lie — it turns "I don't remember
what I paid" into "it was free" and reports an infinite-looking return.

## Decision

**There are two ROIs and there is never one.** `incremental_roi` and
`investment_roi` are separately named on the wire, computed from separate
numerators over separate denominators, and no code path produces a figure called
`roi` alone. A future single headline number is a new ADR, not a convenience.

Let `P(g)` and `V(g)` be the retained grade distribution and the graded market
value for one company, `sale_costs(x)` the selling fee incurred selling at price
`x`, and

```text
grading_costs = grading_fee
              + outbound_shipping
              + return_shipping
              + insurance
              + miscellaneous
```

— the selling fee deliberately excluded, because it is paid out of proceeds
rather than committed up front.

```text
graded_proceeds       = Σ_g P(g) · ( V(g) − sale_costs(V(g)) )
raw_opportunity_value = raw_market_value − sale_costs(raw_market_value)

incremental_profit    = graded_proceeds − raw_opportunity_value − grading_costs
CapitalAtRisk_incr    = raw_opportunity_value + grading_costs
incremental_roi       = incremental_profit / CapitalAtRisk_incr

investment_profit     = graded_proceeds − acquisition_cost − grading_costs
CapitalAtRisk_inv     = acquisition_cost + grading_costs
investment_roi        = investment_profit / CapitalAtRisk_inv
```

**The selling fee applies to both branches of the incremental comparison.** A
raw sale incurs it too, so charging it only to the graded side would penalise
grading with a cost the alternative also pays.

**The fee is applied per outcome, inside the sum, never to the expected value.**
For a flat or percentage fee the two are identical; for a capped or tiered one
they are not, and the shape of the fee is #58's decision rather than this one's.

**An absent acquisition cost makes the investment figures `null`, never zero.**
`investment_profit` and `investment_roi` are `null` and the response carries
`investment_roi_reason: "acquisition_cost_not_supplied"`. The incremental
figures are still computed and returned — they do not depend on it.

**A zero denominator is `null`, not infinity.** `CapitalAtRisk` of zero yields a
`null` ROI with reason `no_capital_at_risk`; the corresponding profit figure is
still reported. Neither denominator can be negative: both are sums of
non-negative quantities.

**ROI is a ratio, serialised as a decimal string** — `"0.625"`, not `62.5` and
not a JSON number — for the reason #56 already serialises `amount` as a string.
It is a `Decimal` quantised to four places, `ROUND_HALF_UP`; `Money`'s two-place
quantisation is for money and does not apply to a ratio.

**The labels are the user's, not the domain's.** `incremental_roi` is presented
as **"Return on grading"** and `investment_roi` as **"Return on your
investment"**. #66 shows the first by default, because the collector who already
owns the card is the ordinary case.

### Worked examples

These are hand-calculated and become #62's fixtures verbatim.

**1 — owned card, no selling fee.** `P(9) = 0.5, V(9) = 200`;
`P(10) = 0.5, V(10) = 320`; `raw_market_value = 100`; grading 40, outbound
shipping 12, return shipping 8, insurance 0, miscellaneous 0; selling fee 0.

```text
graded_proceeds       = 0.5·200 + 0.5·320          = 260.00
raw_opportunity_value = 100 − 0                    = 100.00
grading_costs         = 40 + 12 + 8                =  60.00
incremental_profit    = 260 − 100 − 60             = 100.00
CapitalAtRisk_incr    = 100 + 60                   = 160.00
incremental_roi       = 100 / 160                  =   0.6250
```

The rejected costs-only basis reports `100 / 60 = 1.6667` on these same inputs.

**2 — the same card, 10% selling fee.**

```text
graded_proceeds       = 0.5·(200−20) + 0.5·(320−32) = 234.00
raw_opportunity_value = 100 − 10                    =  90.00
incremental_profit    = 234 − 90 − 60               =  84.00
CapitalAtRisk_incr    = 90 + 60                     = 150.00
incremental_roi       = 84 / 150                    =   0.5600
```

**3 — the same card, acquisition cost 45.** Both figures, from one analysis.

```text
incremental_profit    = 84.00        incremental_roi = 0.5600
investment_profit     = 234 − 45 − 60             = 129.00
CapitalAtRisk_inv     = 45 + 60                   = 105.00
investment_roi        = 129 / 105                 =   1.2286
```

0.5600 and 1.2286 describe the same card on the same day. A response that
reported either as "ROI" would be reporting a number the reader cannot
interpret.

**4 — acquisition cost absent.** `incremental_profit = 84.00`,
`incremental_roi = 0.5600`; `investment_profit = null`, `investment_roi = null`,
`investment_roi_reason = "acquisition_cost_not_supplied"`.

**5 — nothing at risk.** `raw_market_value = 0` and every cost line item 0:
`incremental_profit` is reported, `incremental_roi = null` with
`no_capital_at_risk`.

## Consequences

The incremental figure is available for the case the product is actually built
for — a collector who owns the card and does not recall what they paid — and it
is available without inferring anything. That is what the shared-basis option
would have cost, and it is the largest single reason this basis was chosen.

The number is smaller than users will expect. "Return on grading" against a
denominator that includes the card reports 62.5% where a competitor reports
166.7% on identical inputs, and a user comparing the two will conclude this
product is pessimistic rather than that it is measuring something else. The
label has to carry that, which is real work for #66 and not merely a caption.

Two ROIs is two things to explain on a phone, on a screen that already has six
cost fields and five optimization modes. The mitigation — showing "Return on
grading" by default and the investment figure only once an acquisition cost has
been entered — is a UI decision this ADR constrains but does not make.

Excluding the selling fee from `CapitalAtRisk` while including it in proceeds is
correct and is not obvious; a reader checking the code against this file will
find `grading_costs` deliberately short of §46's six line items. It is named
here so that difference reads as a decision rather than an omission.

Applying the fee inside the sum costs nothing today — every fee shape §46 admits
is affine, so it is arithmetically identical to applying it to the expected
value — and it is written this way so that a capped fee, if one is ever
configured, does not silently produce a wrong expectation. This forecloses the
tempting simplification of computing `EV` once and netting fees afterwards.

Serialising a ratio as a string pushes percentage formatting to the client, in
exchange for never routing a decision figure through a binary float. `Money`
already rejects floats outright for the same reason; a ratio that reached the
wire as `0.5600000000000001` would undermine that in the one field a user reads
as a verdict.

`null` with a stated reason rather than an omitted field means #65's response
shape is stable whether or not an acquisition cost was supplied, and a client
can distinguish "not applicable" from "the server did not send it" — the rule
#56 already set for a price the snapshot does not hold, and #91's rule that a
thing not measured is never reported as a number.
