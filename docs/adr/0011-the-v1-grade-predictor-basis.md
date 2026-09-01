# ADR 0011 — The V1 grade predictor is a declared-uncertainty baseline

- **Status:** accepted
- **Date:** 2026-09-02
- **Refs:** M8, #219, #9, spec §22, §24, §25, §26, §27, §57, §58, §59, §63, §69

## Context

M8 predicts, per grading company, a probability distribution over grades from the
neutral condition representation M7 produces. Epic #9 was decomposed against two
facts, and both are false today.

**There is no ground truth.** #165 landed on 2026-09-01 and gives the corpus its
first target: `grading_outcomes`, one row per grading submission, holding what a
company actually issued. It holds **zero rows**, and will until submitted slabs
come back — weeks and a grading fee per label. Nothing in the schema, in
`datasets/manifests/`, or in `ml/evaluation`'s `truth.py` can supply `y`.

**There are no machine-readable published tolerances either.**
`packages/grading-companies/src/tcg_grading_companies/reference.py` sets
`rules = EMPTY_RULES` on every record, on purpose: each company's standard is
copyrighted text this repository does not reproduce. `grading_rules` therefore
stores the *identifier* of a standard and nothing a mapping could read. A
rules-driven predictor is not merely unbuilt; it has no input.

**And the corpus is small by design.** `pokemon-condition-v0.2.0` is 14 physical
copies and 28 images, split 20/4/4 — a **test split of four images**. ADR 0008
predicted exactly this: *"M7 and M8 must be planned against hundreds"*, and its
answer to a corpus too small for a claim is already on the record — *"M8 may find
the approved corpus too small to claim calibration. The answer then is to say so,
not to widen the allow-list."* That is not reopened here.

So the question is not which model to train. It is what a V1 predictor may
legitimately be when nothing can be fitted, what the milestone may claim when
nothing can be scored, and where the code lives. Eight issues (#220–#228) each
quote one of the answers, which is why they are settled in writing first — the
#57, #171 and #42 precedent. This is **not** one of spec §78's four unresolved
decisions; like [ADR 0010](0010-what-surface-defects-are-measured-against.md) it
was raised by an issue, and no §78 entry covers it.

Eight answers were genuinely on the table and are recorded here as rejected.

### An all-refusing V1 — rejected, and it was the honest-looking option

Every `predict_grade` keeps refusing, exactly as `_ReferenceAdapter` does today.
This is a coherent product: spec §2.7 makes `insufficient_information` a
legitimate output, §44's third recommended action renders end to end, and no
number is ever claimed that nobody measured. It deserved the record rather than a
dismissal, and it fails on three counts.

It **discards evidence the assessment holds.** A `ConditionAssessment` with four
whitened corners and a measured 70/30 centering is not silence; refusing to say
anything about it is not more honest than saying *"probably not a 10, and I have
not been calibrated"* — it is less informative at the same confidence.

It **produces nothing the benchmark can ever score.** #222 lands before the
predictors (#188's rule) and would have no output to read; #223–#225 become three
packages that return a constant; #227 records a refusal; #228 wires it. M8's
acceptance criterion becomes unmeetable not because the evidence is missing but
because nothing was built to measure.

And it **misplaces the refusal.** The product's decision to decline is §44's, made
by `recommend` against thresholds that already exist (#64) and that already yield
`insufficient_information` on a distribution too wide to act on. Refusing one
layer earlier takes that judgement away from the component the spec assigns it to.
#187 settled the same argument for the condition step — *"too little to act on
stays the company models' and #64's judgement"* — and this ADR does not reverse it
one layer up.

### A fitted probability model — rejected; there is nothing to fit

The obvious answer, and the unavailable one. Zero labels, and a four-image test
split even once labels exist. Not deferred on preference: it re-enters the moment
`grading_outcomes` holds test-split rows, through the same seam this ADR builds.

### Fitting a calibrator anyway — rejected for the same reason, one layer up

Spec §25 makes calibration mandatory, and the tempting reading is that a
mandatory thing must be produced: fit a temperature or an isotonic map over
whatever labels exist, report the curve, and note the sample size in small type.
It fails on the same arithmetic as the model it would correct. A calibrator fitted
on a handful of labels is noise wearing a curve, and a calibration plot is
*believed* in a way a wide distribution is not — it is the one output that looks
like evidence of trustworthiness. Producing one from four images would be the
confidently-wrong failure this project's invariants exist to prevent, committed in
the section of the spec that exists to prevent it. §25 asks that calibration be
**evaluated**, and evaluating it honestly at zero support means reporting that it
could not be measured.

### Collapsing the tail into §24's `7_or_lower` bucket — rejected on vocabulary

Spec §24's own example emits `{"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower":
0.02}`, and every layer already accepts it: `Grade` parses it,
`GradeScale.supports` admits a bucket whose value names a scale point, and
`expected_value` prices one. It was rejected because a bucket says nothing the
full ladder cannot, and costs three translations.

`grading_outcomes` **refuses buckets** — *"a tail is what a model emits when it
will not commit to one point, and a slab prints one point"* — so #222 would need a
distance rule from a bucket to an issued grade that nothing else in the system
needs. The 55 `(company, grade)` pairs `GET /cards/{id}/market` serves come from
`GradeScale.ordered`, which is **points only**, so a bucket never has a price of
its own and resolves through `_resolve`'s derived floor — the lowest price among
the grades it covers, which depends on what the snapshot happens to hold rather
than on what the model meant. And epic #9's ±1 rule is a step on the company's own
ladder; a bucket is not a step.

Equal mass spread over the tail grades states exactly what the bucket states, in
the vocabulary the outcomes table, the market ladder and the benchmark all already
speak. Buckets stay legal everywhere they are legal today; V1's predictors simply
do not emit one.

### A named minimum test-split size — rejected as a number nobody measured

"Claim §27 once the test split holds at least N outcomes per company" is easy to
read and easy to check, and N would have been invented. Twenty was the candidate.
Nothing measured it, and a threshold nobody agreed is precisely what this issue
exists to stop being written.

### A function-local import inside the adapter — rejected as a package cycle

`PSAAdapter.predict_grade` imports `tcg_ml_grading_psa` inside the function,
behind an optional extra only the worker installs — the `jobs._advance` precedent
that is the reason the API and worker split is real at all. It fails one level up
from where that precedent works. `ml/grading/psa` must import `GradePrediction`
and `GradeScale` **from** `packages/grading-companies`, so the reverse import is a
genuine cycle between two workspace members rather than merely a deferred one; and
it breaks that package's stated invariant — *"depends on `tcg-domain` and nothing
else"*, written in its `pyproject.toml` and again in its `__init__`. `_advance`
defers an import inside the service that owns the wiring; a domain package
deferring an import of an ml package is a different thing wearing the same shape.

### Worker-side adapter subclasses — rejected on the fourth-company test

`PSAPredictingAdapter(PSAAdapter)` in `tcg_api.analysis`, overriding
`predict_grade`, with `companies.py`'s adapters left refusing. It works, it
touches the package not at all, and it costs the one thing spec §22 exists to buy:
a fourth company would need a new adapter *and* a new subclass in another package,
where the port's own docstring promises *"adding CGC or ARS later must require
only a new adapter, and no change to any caller."*

### A registry row for the baseline — rejected; the registry would have to lie

`model_bundles` is the natural home for anything carrying a version, and it cannot
hold this one. `version_names_its_model` constrains `model_version` to
`{model_name}-vX.Y.Z`, and `training_dataset_version` (a foreign key to a
published corpus), `training_config`, `metrics` and `artifact_location` are all
NOT NULL. A deterministic baseline trained on nothing, measured against nothing
and stored nowhere satisfies none of them. #189 wrote the rule down before the
question was asked: *"a row names a trained artifact with a dataset version and
metrics, and null-filling those for code would make the registry lie."*

## Decision

Six answers, in the order #219 asks them.

**1. A V1 predictor consumes a `ConditionAssessment`, and forms a distribution by
a versioned deterministic mapping whose spread is declared rather than fitted.**

The input is `tcg_domain.condition.ConditionAssessment`, rehydrated by the worker
from `analyses.condition_details` — #187's seam, and M8 reads the stored document
rather than the analyzers. #221 narrows `predict_grade`'s `object` parameter to
that type.

Each company gets its own mapping from the assessment's axes to a distribution
over that company's scale. Its constants are **declared in the package and
recorded beside the output**, the way the four analyzers' thresholds sit beside
every `condition_details` document. The spread is the model's declaration of how
little it knows, not a fitted posterior, and that is stated in the output rather
than only in this record: `model_confidence` is bounded above both by the
assessment's own `confidence` and by a declared per-version ceiling, so no V1
prediction can present itself as more certain than the evidence it read or than an
uncalibrated mapping is entitled to be.

**A thin assessment widens; it does not refuse.** There is no coverage gate and no
confidence gate on the prediction step — epic #9's rule, and #187's for the step
before it. A predictor refuses in exactly one case: when its input is itself a
refusal. A top-level `{"insufficient_information": reason}` in `condition_details`
propagates as `INSUFFICIENT_INFORMATION` carrying that same reason, and a
`ConditionAssessment` with every axis refused is still an assessment and still
produces a distribution — a very wide one.

Every distribution is a `GradeDistribution`, so §63 is enforced by its constructor
and nothing writes a second validator, and every key is a grade the company's own
`GradeScale` supports.

**2. §27's target stands, and M8 claims it met only when a confidence interval
says so.**

The target is unchanged: **at least 80% of predicted grades within ±1 actual grade
on the held-out test split, per company**. ±1 is one step on that company's own
ladder, never ±1.0 of arithmetic — BGS has 19 points where PSA and TAG have 18,
and treating the gap as a number makes BGS look better than PSA for free.

It is claimed met only when the **95% Wilson score lower bound of the observed
within-±1 rate clears 0.80**, for that company, on that split, with the count
printed beside the figure. The interval is the whole answer to "how many labels is
enough", and it scales itself: a perfect four-for-four on today's test split has a
lower bound near 0.51, and even a flawless record cannot clear 0.80 until that
split holds **16 outcomes for that company**. At the splitter's current
proportions the test split is about one image in seven, so the corpus behind such
a claim is several hundred physical copies — ADR 0008's "hundreds", arrived at
from the other end.

**What M8 claims now**, and this is the milestone's closing condition: three
per-company predictors exist, each emits a §63-valid distribution over its own
company's scale, #222's harness scores them per split with counts beside every
figure, and every figure needing an issued grade refuses with a reason. M8 closes
on that, as M7 closed on deliberately partial baselines. Re-entry is
`grading_outcomes` rows on test-split copies, reaching `ml/*` through the manifest
and never the database ([ADR 0009](0009-the-dataset-store-as-a-database-domain.md)).

**3. Calibration is reported, never fitted, and its absence is a value in the
output.**

The sentence M8 stands behind:

> **M8 claims no calibration. No predictor's probabilities have been compared with
> an issued grade, and until `grading_outcomes` holds test-split rows the harness
> reports `insufficient_information` for every §25 figure rather than a curve
> fitted to nothing.**

The calibration curve, Brier score, expected calibration error and log loss are
all implemented and all run; each refuses independently at zero support, as the
one-key `{"insufficient_information": reason}` object, in the vocabulary
`ml/evaluation` already uses for a figure with no examples. Spec §25's "mandatory"
is satisfied by evaluating and reporting, which is what it asks for; it is not
satisfied by a curve over four images, and a fitted calibrator over a handful of
labels is noise wearing a curve. One — temperature scaling, isotonic regression —
re-enters behind a bumped `EVALUATION_VERSION`, fitted on train and validation
outcomes only, because calibrating against the test split stays forbidden (§27).

**4. A V1 distribution covers the company's full ladder. No bucket.**

Every key of that company's `GradeScale` — 18 for PSA, 18 for TAG, 19 for BGS —
with mass spread across the low grades where a bucket would have collapsed them.
Buckets remain legal in `GradeDistribution`, on `GradeScale.supports` and in
`market_observations`, and remain priced by `expected_value`; a later learned model
may emit one. V1's do not.

**5. `predict_grade` stays on the adapter, the predictor is injected into it, and
the package dependency points from `ml/grading/*` to `packages/grading-companies`.**

The three packages `ml/grading/{psa,tag,bgs}` — already workspace members, still
empty — depend on `tcg-grading-companies` for `GradePrediction` and `GradeScale`.
That package acquires no dependency and keeps its `tcg-domain`-only invariant.

`_ReferenceAdapter` gains an optional predictor: a callable
`(ConditionAssessment) -> Uncertain[GradePrediction]`, with the type named in
`port.py`. An adapter constructed without one **keeps raising
`GradePredictionUnavailable`**, so `ADAPTERS` — which the API image imports through
`routers/grading.py` — refuses exactly as it does today, and the three refusal
tests stay true unchanged.

The worker supplies the predictors from a lazily-imported wiring module,
`tcg_api/analysis/grading.py`, built as `condition.py`'s twin and imported inside
`jobs._advance`. The three ml packages join the `worker` extra,
`[tool.uv.sources]` and both CI image assertions at #227 — the first
`services/api` importer, which is when an ml package joins those lists and never
before. `test_import_purity.py` needs no change to keep holding: nothing reachable
from `tcg_api.main` imports a `tcg_ml_` module, because the injection point is a
parameter rather than an import.

The step runs where the condition step runs — inside the claim, after
`assess_condition`, in the same transaction. It may run before card confirmation
for precisely the reason the condition step may: its input is the neutral
representation and no predictor reads the card's identity. §20 is untouched,
because §20 governs using an unconfirmed identification for economic analysis, and
a grade distribution is not one. #227 owns the mechanics.

This keeps spec §22 literally: `predict_grade()` is an adapter responsibility, the
adapter performs it, and the model it performs it with is supplied by the process
that has one.

**6. A V1 baseline is a code constant. It is not a registry row, and epic #9's
registry checklist item re-homes a second time.**

Each predictor's `model_version` is a package constant in the established
heuristic grammar — `grading-psa-heuristic-v0.1.0` beside
`image-quality-heuristic-v0.3.0` — composed into the analysis's
`model_bundle_version` exactly as `CONDITION_VERSION` composes the four analyzers,
so a package bump cannot be forgotten. `model_bundles` stays empty.

Epic #9 carries *"Model registry entries; versioned bundles referenced explicitly,
never `/latest/`"*, re-homed there from M7 by #8's closing comment. It re-homes
again, and the reason is stated rather than carried silently: **a registry row
names a trained artifact, the first trained artifact needs labels, and labels need
returned slabs.** The row is written by the issue that trains the first model;
`tcg-register-model-bundle` and the promote path are ready for it and are not
exercised by M8. This is not a third deferral in waiting — it is a statement that
the registry's precondition is the same one gating calibration, and both are named
in the re-entry triggers below.

### `grading_rules_version` records what was in force, not what was consulted

The one §57 field nothing writes becomes real here. A V1 predictor reads no
machine-readable rules and still records the version — #187's exact reasoning for
`model_bundle_version`: the record says which versions were **in force**, not which
were consulted.

It is written **at the claim**, by `record_reproducibility`'s sixth parameter
(#227), as a `+`-joined composite over `ADAPTERS` in slug order, each part resolved
by `rules_in_force` against the `grading_rules` **table** and never read from the
package — `GET /grading-companies`' rule, for the same reason. All three are
composited rather than the selected company's alone because at the claim no company
has been selected: the economic configuration naming them arrives later, through
its own endpoint, while the analysis is already `analyzing`. All three standards
were in force, and the record says so.
`trg_analyses_reproducibility_immutable` permits the NULL-to-value write once,
which is all this needs.

This does not contradict #165, which keeps `grading_rules_version` off
`grading_outcomes` as derivable. That column would have answered *which standard
governed this slab*, which `rules_in_force(company, returned_at)` answers better
later. This one answers *which standards were in force when this analysis ran*,
which only the run can know. Different questions, both correct.

### Re-entry triggers, so this decision is reversible without re-deciding it

A trained per-company model re-enters through the injected-predictor seam and
#189's registry, superseding a baseline's constant with a registry version; nothing
here has to be undone for it. A fitted calibrator re-enters behind a bumped
`EVALUATION_VERSION`, on train and validation outcomes. §27's claim re-enters the
moment the Wilson bound clears, with no further agreement needed — which is what
fixing the rule now buys. A bucketed distribution re-enters if a learned model has
a reason to emit one; every layer already accepts it.

Each of those is a new issue against an unchanged ADR. A different *basis* — a
rules-driven predictor, a universal condition-to-grade mapping, one shared model
across companies — earns a new ADR; this one is not rewritten.

### Review trigger

Reviewed when `grading_outcomes` first holds a test-split row; when a company
publishes a standard revision that changes its scale; when a trained bundle is
registered; or if the §57 or §63 contracts change. **The trigger is a scheduled
re-read, not a notification.** The three `GradingRules` records this ADR reasons
about were verified 2026-08-24 and are fresh; under
[ADR 0006](0006-the-v1-market-data-provider.md)'s ninety-day rule the next reading
is due by **2026-11-22**, and it happens whether or not anything prompts it.

## Consequences

**What this makes easy.** #221 through #228 can each be written without re-asking
any of the six questions: #221 has a type to narrow to, #222 has a scoring rule and
a refusal position, #223–#225 have a shape and a stopping line, #226 has a resolved
dependency direction, #227 has a placement and a sixth reproducibility parameter,
and #228 has something to serve. The benchmark scores three real outputs rather
than three constants. M9 gets distributions to render, and §44's `recommend` gets
its first real input. And the honest answer at every stage is produced by machinery
that already exists — the one-key refusal object, `Uncertain`, `Confidence`, counts
beside every metric — rather than by new special-casing.

**What this makes expensive.** Three hand-declared mappings whose constants nobody
has measured, each of which the first trained model replaces wholesale: the work is
deliberately throwaway, and #224's honesty test — TAG's predictor must not be PSA's
with a different version string — makes it three separate pieces of throwaway work
rather than one. The §27 claim now needs several hundred graded copies before it can
be made at all, which is a real cost and a previously unstated one.
`grading_rules_version` becomes a composite string rather than a key into
`grading_rules`, accepting the same open seam `analyses.model_bundle_version`
already carries. And an injected predictor is a seam a reader has to follow from the
adapter to the worker's wiring module to see what actually runs.

**What this forecloses.** It forecloses a V1 that claims a calibrated probability,
and with it any UI copy that presents one — M9 renders a distribution whose
uncertainty is declared, and §44's `insufficient_information` will be a common
outcome rather than an edge case. It forecloses reading M8's evaluation record as
evidence about model quality: at zero labels the harness measures that the pipeline
runs, not that it is right. It forecloses a shared `ml/grading/common` package —
where two predictors genuinely share arithmetic it goes in the domain — and with it
the quiet path to the universal condition-to-grade mapping the master architectural
rule forbids. And it forecloses the registry acquiring its first row in this
milestone.

The decision is scoped to **what a V1 grade predictor is and what M8 may claim**.
It reproduces no company's published standard, and the reason `rules` is empty does
not change because a model would find the text useful. It does not reopen
[ADR 0006](0006-the-v1-market-data-provider.md),
[ADR 0007](0007-roi-and-the-capital-at-risk-basis.md) or
[ADR 0008](0008-permitted-training-image-sources.md) — in particular, a model
wanting more data does not make a rejected source permitted, and **this record must
not be cited as having settled ADR 0008's standing artwork question**. It does not
decide whether a `ManifestMember` may carry an issued grade; #165 raised that as an
ADR 0008 question and #220 owns it. It does not design the promote-and-retire path,
which belongs to the first trained artifact. And it chooses no model architecture:
it fixes only what the interval before one looks like.
