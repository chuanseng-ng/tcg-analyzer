# Measuring the image-quality gate against real photographs

- Date: 2026-08-31
- Refs: M7, #190, #171, #176, #181, spec §19, §69

Spec §69/M7's first deliverable is "image-quality model improvements", and this
project does not replace a working component on style — #171 is the precedent:
photograph, measure, decide from evidence. This document is that measurement
for spec §19's gate. It changes nothing: every improvement it motivated is a
filed issue (#206, #207, #208), each behind the same `assess` interface, and
no threshold moved here, because a changed number is a version bump that must
trace to a measured failure — which this document now supplies.

## Method

Measured 2026-08-31 with a throwaway instrument (never committed, #171's
rule): for each of the corpus's 28 photographs, `detect(original bytes)` then
`assess(original bytes, geometry=...)` — the worker's
`_locate_judge_and_straighten` shape minus the warp. The corpus path skips the
gate deliberately (`tcg_api/datasets/normalization.py` says why), so this is
the first time the gate has ever run over real photographs; until now its
verdicts were calibrated entirely against synthetic fixtures.

- **Corpus:** #181's 28 images (14 owned cards, front and back), 3024×4032
  px, shot at ~33–43 px/mm on the card. The photographs are held outside the
  repository (ADR 0008) — this record is the finding, not the images.
- **Versions:** `image-quality-heuristic-v0.2.0` at default thresholds,
  geometry from `card-detection-opencv-v0.3.0` — the detector as fixed by
  #192/#193, after #176's frame-hugging refusal.
- **Access:** read-only — SELECT and storage GET only; the corpus database is
  byte-identical before and after.
- **Telegram simulation:** the real Telegram-compressed copies from the ADR
  0010 session no longer exist on disk, so each original was re-encoded the
  way Telegram "send as photo" does — longest side resized to 1280 px, JPEG
  quality 87 — and run through the same instrument. Those rows are
  **simulated**, and labelled so throughout.
- **Reproducibility:** two full runs produced identical results; the gate and
  detector are deterministic over stored bytes.

## The annotator's judgement

The ground truth, from the person who shot and annotated all 28 during #181:

- Every photograph is a **bare, unsleeved card** — no sleeve, top-loader or
  holder anywhere in the corpus.
- Every frame held **exactly one card** and nothing else card-shaped.
- All 28 were usable for annotation. Three fronts were **marginal** — the
  kind they would reshoot: `mew_cn_front`, `wally_jp_front`,
  `ditto_jp_front`, for glare on the holographic surface, lighting, and
  framing angle, in combination.

## Results

Gate verdicts over the 28 originals: **6 good, 18 poor, 4 unusable** — against
an annotator who used all 28. Every disagreement traces to exactly two
conditions; the six photometric conditions (blur, resolution, glare, exposure,
darkness, brightness) detected nothing on any original, and all five geometric
conditions were decidable on every image (the detector found the card 28/28).

| image | status | score | detected | border margin | card px/mm | Telegram-sim status |
| --- | --- | --- | --- | --- | --- | --- |
| ditto_jp_back | poor | 0.4061 | `sleeve_obstruction` poor (1.20) | 0.1126 | 34.9 | poor |
| ditto_jp_front | unusable | 0.0 | `multiple_cards` unusable (2) | 0.0833 | 36.3 | unusable |
| feraligatr_en_front | good | 0.695 | — | 0.1211 | 32.8 | poor |
| feraligatr_en_back | poor | 0.3978 | `sleeve_obstruction` poor (1.22) | 0.1198 | 35.0 | poor |
| mew_cn_front | poor | 0.4652 | `sleeve_obstruction` poor (1.09) | 0.0308 | 41.3 | poor |
| mew_cn_back | poor | 0.4585 | `sleeve_obstruction` poor (1.10) | 0.0508 | 40.8 | poor |
| meowth_cn_back | poor | 0.4655 | `sleeve_obstruction` poor (1.09) | 0.0365 | 41.6 | poor |
| meowth_cn_front | unusable | 0.0 | `multiple_cards` unusable (2); `sleeve_obstruction` poor (1.09) | 0.0716 | 39.0 | unusable |
| mimikyu_jp_back | poor | 0.4743 | `sleeve_obstruction` poor (1.07) | 0.0234 | 43.0 | poor |
| mimikyu_jp_front | poor | 0.4834 | `sleeve_obstruction` poor (1.05) | 0.0430 | 40.8 | poor |
| clobbopus_jp_back | good | 0.8849 | — | 0.0820 | 38.0 | poor |
| clobbopus_jp_front | unusable | 0.0 | `multiple_cards` unusable (2); `sleeve_obstruction` poor (1.16) | 0.0439 | 41.2 | unusable |
| wally_jp_back | good | 0.8863 | — | 0.0664 | 38.8 | poor |
| wally_jp_front | poor | 0.4654 | `sleeve_obstruction` poor (1.09) | 0.0521 | 41.2 | poor |
| lapras_jp_back | poor | 0.4589 | `sleeve_obstruction` poor (1.10) | 0.0648 | 40.3 | poor |
| lapras_jp_front | good | 0.845 | — | 0.0729 | 39.4 | poor |
| psyduck_jp_front | poor | 0.4184 | `sleeve_obstruction` poor (1.18) | 0.0768 | 39.4 | poor |
| psyduck_jp_back | poor | 0.4668 | `sleeve_obstruction` poor (1.09) | 0.0625 | 40.0 | poor |
| bulbasaur_jp_front | unusable | 0.0 | `multiple_cards` unusable (2); `sleeve_obstruction` poor (1.08) | 0.0755 | 39.4 | unusable |
| bulbasaur_jp_back | good | 0.815 | — | 0.0807 | 38.9 | poor |
| ivysaur_jp_back | good | 0.8324 | — | 0.0820 | 38.5 | poor |
| ivysaur_jp_front | poor | 0.4562 | `sleeve_obstruction` poor (1.11) | 0.0729 | 39.7 | poor |
| venusaur_jp_front | poor | 0.4367 | `sleeve_obstruction` poor (1.14) | 0.0435 | 42.2 | poor |
| venusaur_jp_back | poor | 0.464 | `sleeve_obstruction` poor (1.09) | 0.0204 | 41.7 | poor |
| pikachu_jp_front | poor | 0.4588 | `sleeve_obstruction` poor (1.10) | 0.0562 | 40.7 | poor |
| pikachu_jp_back | poor | 0.4661 | `sleeve_obstruction` poor (1.09) | 0.0608 | 41.1 | poor |
| milotic_en_front | poor | 0.485 | `sleeve_obstruction` poor (1.05) | 0.0625 | 40.7 | poor |
| milotic_en_back | poor | 0.472 | `sleeve_obstruction` poor (1.08) | 0.0755 | 40.4 | poor |

The `detected` column shows the finding's measurement; `border margin` is the
geometry's `border_margin_fraction`; `card px/mm` is estimated from the
detected quad's side lengths against 63×88 mm. Every Telegram-simulated row
additionally detected `low_resolution` POOR and nothing else new.

## Findings

- **A phantom second candidate hard-refuses one-card scenes — filed as
  #206.** The detector reported `candidates = 2` on four fronts, the gate
  turned each into `multiple_cards` UNUSABLE (`card_count_unusable = 2.0` is
  an immediate refusal), and the annotator confirms one card per frame,
  always. 4 of 28 — 14% of exactly the close-range framing the primary
  training-image source produces — would have been refused at upload for a
  card that isn't there. This is the gate's worst behaviour on the corpus,
  and its mechanism (most plausibly the artwork window surviving grouping as
  a second card-like quad) is #206's to establish.
- **`sleeve_obstruction` reads bare cards as sleeved, 21 of 28 — filed as
  #207.** Measured `enclosing_ratio` ran 1.05–1.22 against
  `sleeve_ratio_poor = 1.02` on photographs with no sleeve anywhere. It is
  also unstable: recompression alone made `venusaur_jp_front`'s finding
  vanish and `wally_jp_back` gain one. Because the §19 score is the minimum
  condition margin, this one false POOR pins 18 images' `quality_score` at
  ~0.40–0.49 with everything else clean.
- **The glare measurement is blind to what the annotator saw — filed as
  #208.** Glare on the holo surface is a named reason the three marginal
  fronts were marginal, yet the measured glare area never exceeded 6.4e-05 of
  the frame against a POOR line of 0.005 — on all 28, two orders of magnitude
  of headroom. Counting near-saturated pixels (level 250) over the whole
  frame cannot see diffuse sheen on the card. This is the one place the gate
  is *lenient* where a person is strict; everywhere else it is strict where a
  person is lenient.
- **Telegram-class uploads are warned, not refused — recorded, no issue.**
  Every simulated copy (960×1280) detected `low_resolution` POOR: 960 px
  sits under `min_short_edge_poor = 1000` and comfortably above
  `min_short_edge_unusable = 640`. So the gate answers "continue but inform
  the user", which is defensible: at the measured ~11–13 px/mm the artifact
  still supports the coarse axes, and ADR 0010 already refuses the fine
  surface classes at *any* source resolution. Refusing Telegram-class uploads
  outright would mean raising the unusable floor past 960 — a product
  decision this measurement does not force, because nothing measurably failed.
  Note the floor is on the *frame's* short edge: a distant card in a large
  frame is caught by `insufficient_card_size` (area), not by resolution, and
  no such case exists in this corpus to measure.
- **#176's border-margin hole is moot on this corpus.** The measured
  `border_margin_fraction` never fell below 0.0204 — four times the POOR line
  (0.005) — because the detector now refuses frame-hugging quads before the
  gate sees geometry, and because a photographer aiming at the card leaves
  margin. `border_margin_unusable = 0.0` (reachable only at exactly zero)
  stays as it is: no measured failure, no change.
- **The photometric conditions have wide, healthy margins.** Blur variance
  ran 419–3161 against a POOR line of 120; exposure range 160–223 against 60;
  perspective ratio at worst 1.073 against 1.12. On well-lit close-range
  photographs the photometric half of the gate neither helps nor hurts — the
  geometric half decides everything, which is where both false-positive
  classes live.

## What this does not decide

No threshold moved and no version bumped — #206, #207 and #208 own the fixes,
and each change bumps the owning package's version constant. The heuristic's
one measured *ceiling* (diffuse glare, #208) is not by itself grounds for a
learned quality model: if #208 concludes the heuristic cannot see it, a
replacement enters through a benchmark with its own issue and ADR, exactly as
the epic's decomposition decided for the axis analyzers. `assess`'s contract
and §19's eleven conditions are unchanged, and a fourth verdict remains
unwritten.
