# `ml/surface`

Classifies one card side's surface from the normalized artifact — spec
§16/§17, issue #185.

`classify(data, side=..., card_frame=...)` takes the artifact's bytes, which
side it shows (a `Defect` names its side, unlike a `RegionFinding`) and the
card rectangle the caller derives from the artifact's **stored**
normalization record (never the image boundary — #194 put a 24 px background
margin around the card), and answers `Uncertain[SurfaceAssessment]`:
findings for what was seen — a clean face is an empty tuple, because §16 has
no `clean` — and a class-level refusal with its own reason for every class
this version does not assess. The whole side refuses only when the bytes do
not decode or the frame is too small to hold a face inside the border
exclusion.

## The fine classes are refused, and that is the point

`scratch`, `print_line`, `print_dot` and `gloss_issue` are below the
12 px/mm artifact's sampling limit — a hairline scratch is 0.1–0.6 px — and
ADR 0010 is the recorded decision that they answer
`insufficient_information` against the artifact rather than being guessed.
The refusal is `SurfaceAssessment.not_assessed`, a verdict per class, never
a silently omitted label. #175's original-photograph representation
(`image_annotations.representation = 'original'`) is the only route back to
a fine-class signal, and a model reading it is a follow-up gated on enough
such rows existing.

## What v0.1.0 claims, and what it deliberately never does

The baseline reads two signals on the open face: a **stain** is a dark blob
and a **scuff** is a dull whitish abrasion (the edge and corner axes'
near-white claim, on the face). Severity is banded by area. Both classes run
only where the face itself is quiet: on a busy face — foil, dense artwork —
defect texture is indistinguishable from the face's own, and both refuse
class-level rather than guess (the issue's holo clause). A candidate whose
surroundings are busy sits inside artwork and is dropped before anything is
claimed (#176's filter-before-selection). The outer 26 px strip is the edge
and corner analyzers' detection and reference bands and never yields a
candidate here — #184's seam rule, extended to this axis — though the
context ring of a candidate near the face boundary may sample into it.

`dent`, `indentation`, `color_issue`, `registration_issue` and
`factory_defect` are **never emitted** by this baseline: the first two are
depth signals a single normalized view does not carry, `color_issue` needs a
reference image ADR 0004 says does not exist, `registration_issue` needs a
print template nothing holds, and `factory_defect` is a judgement. Each is
refused with that reason. A label the analyzer cannot see is exactly the
confidently-wrong output the architecture forbids. `ml/evaluation`'s
benchmark (#188) measures what the restriction costs against the annotated
corpus, and a learned classifier enters only through that benchmark, behind
this same signature.

The V1 implementation is an OpenCV heuristic versioned by
`SURFACE_VERSION` — a code constant, never a registry row. Changing any
threshold bumps it.
