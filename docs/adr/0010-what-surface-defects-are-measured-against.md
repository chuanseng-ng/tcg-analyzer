# ADR 0010 — What surface defects are measured against

- **Status:** accepted
- **Date:** 2026-08-29
- **Refs:** M7, #171, #175, #176, spec §16, §18, §19, §21, §30

## Context

`ml/normalization` warps every photograph to one standardized artifact at
`pixels_per_mm = 12` — 756×1056 on a 63×88 mm card, exactly 63:88, about
305 dpi, **one pixel = 83 µm**. That artifact is the common input every M7 and
M8 model reads, and #158 stores every annotation as a fraction of it. #159
found, while building the annotation viewer, that a third of §30's defect
classes cannot be represented at that sampling rate, and #171 asked for a
decision: raise the resolution, keep it, or measure surface against something
else entirely. The cost of changing the number is every artifact and every
fingerprint recomputed (`NORMALIZATION_VERSION` composes into
`training_image_fingerprints.hash_version`), which today is zero rows and only
ever rises — so the decision had to come before the first ingestion, not after.

Two of the four answers needed no photograph:

| Judged                         | Size on the card         | At 12 px/mm    |                              |
| ------------------------------ | ------------------------ | -------------- | ---------------------------- |
| Centering, 55/45 vs 60/40      | ~0.3 mm on a 6 mm border | ~3.6 px        | adequate                     |
| Corner whitening, just visible | ~0.2–0.5 mm              | 2.4–6 px       | **was the empirical question** |
| Print line                     | ~50–200 µm               | 0.6–2.4 px     | marginal                     |
| Hairline scratch               | ~10–50 µm                | **0.1–0.6 px** | below the sampling limit     |

A hairline scratch is smaller than one artifact pixel, so §16's `scratch`,
`print_line`, `print_dot` and `gloss_issue` cannot be marked reliably against
this artifact — arithmetic, not opinion. Corners sat in the band where whether
a *person* can judge extent is a question about eyes rather than sampling, and
that is the part real photographs had to settle (see **Evidence**).

Two mechanical facts, found while measuring, that bound every option:

- **The warp multiple falls as the output rises.** `_warp_multiple` is
  `round(source_long_edge / target_height)` capped at `max_warp_multiple = 4`.
  At 48 px/mm the multiple clamps to 1 for every photograph measured, the
  `INTER_AREA` box filter never runs, and the anti-aliasing guarantee the
  module docstring makes is silently voided. "The detail already exists
  upstream at 4×" is true only when the source photograph carries it.
- **The source photograph is the real ceiling.** Ordinary close-up phone
  photographs of a card measured **~34–36 px/mm** at source (3024×4032 frames,
  card filling most of the long edge); casual framing measured 23–25 px/mm. A
  48 px/mm artifact would be an upsample of every one of them.

### Raising `pixels_per_mm` to 16 or 24 — rejected on measurement

Legal candidates are integers (the whole-pixel rule on 63 mm and 88 mm refuses
anything else; the dhash's 9×8 exact-block-average property survives any
integer). At 24 px/mm a corner's whitening band becomes 4.8–12 px and a print
line 1.2–4.8 px — on paper, a real improvement. Measured against real
photographs, it was not one worth 4× the pixels: side-by-side crops of worn
corners at 12/16/24/48 px/mm from ~34–36 px/mm sources were judged **"slightly
more detail at 24 than 12, but not too big a difference overall."** Corner
extent was judgeable at 12. And the class the bump would exist to rescue is not
rescued: a hairline scratch is still 0.24–1.2 px at 24 px/mm, below any
reliable annotation. A 4× storage and model-input cost, a
`NORMALIZATION_VERSION` bump, and a qualified anti-aliasing guarantee, for a
slight gain on classes already adequate and no gain on the classes that are
not.

### Annotating surface against the original photograph — deferred, not rejected

The original photograph is the only representation whose sampling rate rises
with the camera rather than with a threshold: at the measured ~34–36 px/mm a
print line is 1.7–7 px and a scratch 0.3–1.8 px — the only representation in
which fine surface classes are even arguable. But a surface annotation against
it lives in a different coordinate space from every other annotation, which
#158's schema deliberately does not admit, and the artifact gate (#160's 409)
assumes coordinates are fractions of the artifact. That is a schema and UI
change with its own acceptance criteria, and nothing in M6 or early M7 is
blocked without it. It is scoped as **#175** and re-enters there — through an
issue, not by convention.

## Decision

**`pixels_per_mm = 12` stands. `NORMALIZATION_VERSION` is not bumped, and no
artifact or fingerprint is invalidated.**

What §30's classes are measured against, by class:

- **Centering** is measured against the artifact. Adequate by arithmetic
  (~3.6 px on the discrimination §21 defines numerically), and unchallenged by
  measurement.
- **Corners and edges** are annotated against the artifact. Settled
  empirically: extent was judgeable at 12 px/mm on real worn corners, and
  24 px/mm added only slight detail.
- **Coarse surface classes** — `dent`, `indentation`, `stain`, `scuff`,
  `color_issue`, `registration_issue`, `factory_defect` — are annotated against
  the artifact. They are millimetre-scale.
- **Fine surface classes** — `scratch`, `print_line`, `print_dot`,
  `gloss_issue` — **cannot be marked reliably against the artifact, and §16
  therefore cannot claim them from it.** The annotation tool already says so on
  screen (#160), with *I cannot tell* beside the control; that warning stays.
  M7's surface analysis must be permitted to return `insufficient_information`
  for these classes — §16 requires that verbatim, and this ADR is the recorded
  reason it will be exercised rather than a formality. A model that reports
  fine scratches it cannot see is the confidently-wrong failure this project's
  invariants forbid.
- The one route back to a reliable fine-class signal is **#175** — annotating
  surface against the original photograph, with the representation named on the
  row. If it lands, it changes the coordinate space of *surface* annotations
  only; it does not reopen this ADR's resolution decision.

## Consequences

- **Every stored coordinate keeps meaning what it meant.** No re-warp, no
  re-fingerprint, no re-annotation; #159's stored-artifact-is-never-replaced
  rule is never exercised. The change this ADR declines was at its cheapest at
  decision time (zero rows) — declining it means the corpus can now grow
  without a pending invalidation hanging over it.
- **V1 ships with fine surface defects honestly unmeasured.** Until #175 lands
  and a model reads it, `scratch`, `print_line`, `print_dot` and `gloss_issue`
  contribute `insufficient_information` and never a confident finding. That is
  a real product cost: surface is one of §30's four condition axes, and this
  ADR forecloses pretending otherwise.
- **The annotation tool's warning and vocabulary are unchanged.** The fine
  classes stay offered — an annotator who *can* see a defect at 12 px/mm (a
  deep scratch catches light across many pixels) records it with a chosen
  confidence; the warning and *I cannot tell* carry the rest.
- **`max_warp_multiple = 4` keeps its meaning.** At 12 px/mm the measured
  photographs warp at ×3, so the box filter runs and the anti-aliasing
  guarantee holds without qualification.
- **A future resolution change is now strictly more expensive** — it recomputes
  every artifact and fingerprint and orphans every annotation's frame of
  reference. Whoever proposes one starts from this ADR's evidence and writes a
  new ADR; this one is not rewritten.

## Evidence

Measured 2026-08-29 with a throwaway contact-sheet instrument (never
committed): each photograph was detected once, then normalized at 12, 16, 24
and 48 px/mm with the production `normalize()` path and no enhancement of any
kind; the same physical regions — four 7 mm corners and a 12 mm centre — were
cropped from each artifact and displayed side by side at equal size,
nearest-neighbour upscaled so nothing was smoothed, with the achieved warp
multiple and source resolution printed on the sheet.

- **Photographs:** iPhone HEIC originals decoded losslessly to PNG, 3024×4032.
  Close-ups of two card backs (one worn) and one heavily played holo front
  measured **33.9–35.8 px/mm** at source; the same cards photographed casually
  measured 23.0–25.0 px/mm; Telegram-compressed copies of the same photographs
  measured 7.3–7.9 px/mm and were discarded as below even the current
  artifact. Per ADR 0008 (`redistribution_allowed = false` on every approved
  source) the photographs are held outside the repository; this record is the
  finding, not the images.
- **The judgement:** at 12 vs 24 px/mm on the worn corners and holo surface —
  *"24 has slightly more details compared to 12 but is not too big a difference
  overall."* Corner extent judgeable at 12; nothing visible at 24 that decided
  a class invisible at 12. The 48 px/mm column was an upsample of every source
  and was disregarded.
- **Achieved warp multiples** at the measured sources: ×3 at 12 px/mm, ×2 at
  16, ×1–2 at 24, ×1 at 48 — the empirical form of the warp-multiple finding
  above.
- **Side finding, filed as #176:** on the close-ups, `ml/card-detection`'s
  grayscale contour merged the card with its own shadow and returned a
  frame-corner quadrilateral at 76–82% confidence (single candidate,
  `enclosing_ratio` 1.0), and found no card at all on the heavily worn front;
  an HSV-saturation threshold segmented all three cleanly. The measurement used
  the saturation fallback for those photographs, with the quad checked against
  the card's 1.397 aspect before use.
