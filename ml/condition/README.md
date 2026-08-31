# `ml/condition`

Produces the **neutral condition representation** — one company-agnostic
description of the card's physical state, spec §13's tree assembled from the
four axis analyzers (`ml/centering`, `ml/corners`, `ml/edges`, `ml/surface`).

This is the single hand-off point between condition assessment and
grading-company prediction. Nothing in here may know what PSA, TAG or BGS
reward — aggregation into a grade is exactly what §2.2 reserves for the
company models, so this package weighs no axis against another and re-scores
nothing.

`assess(front, back, *, front_card_frame=..., back_card_frame=...)` takes both
sides' normalized artifacts and returns `Uncertain[ConditionAssessment]`;
`compose(...)` is the same assembly from already-produced axis outputs. The
card frames are per-side parameters derived from each artifact's **stored**
`normalization_details` — never from the normalizer's current thresholds.

## What the composition adds, and what it refuses

- **`manufacturing_defects` is derived, never detected**: `factory_defect`,
  `registration_issue`, `print_line` and `print_dot` from the surface
  findings, `rough_cut` from the edge findings. A found defect always
  travels; nothing found is an empty tuple only when every feeding class was
  actually assessed — a refused class or side means *never looked*, not
  *none there*, so the member is `insufficient_information`. The v0.1.0
  surface baseline refuses all four of its feeding classes class-level,
  which makes the refusal today's answer in practice.
- **`eye_appeal` is `insufficient_information` always**: §13 names it,
  nothing defines or annotates it, so nothing can train or verify it.
- **The overall confidence is `min`** over every confidence the answered
  members carry — never a product. When no member carries a single
  confidence (both artifacts undecodable), the composition refuses with
  `no_axis_measured` rather than inventing a number for zero measurements.
  One missing axis never sinks the assessment — M8's models take partial
  evidence and their distributions widen.

## The version

`CONDITION_VERSION` is what the analysis records as its condition bundle
(#187's `model_bundle_version`): the composition logic's own component plus
the four axis versions, joined — a bump anywhere flows through, and changing
the rules here bumps the leading component.

This package is also the first importer of all four analyzers, so its tests
carry the cross-package seam assertions the sibling doc-comments could only
state as convention: edges' 84 px corner exclusion mirrors corners'
`corner_size_px`, and surface's 26 px border exclusion mirrors edges'
`edge_inset_px + 2 * edge_band_px`.

No persistence, no wire schema, no learned model: results carry no version
and no thresholds record — the caller that persists records
`CONDITION_VERSION` and the thresholds' `as_record()` beside them.
