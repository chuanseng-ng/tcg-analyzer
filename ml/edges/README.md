# `ml/edges`

Classifies the four edges of one card side from the normalized artifact —
spec §15, issue #184.

`classify(data, card_frame=...)` takes the artifact's bytes and the card
rectangle the caller derives from the artifact's **stored** normalization
record (never the image boundary — #194 put a 24 px background margin around
the card), and answers `Uncertain[Mapping[EdgeRegion, RegionFinding]]`:
either all four edges, each with an `EdgeLabel`, a confidence, a severity
where a defect is claimed and a bounding box in artifact fractions — or
`insufficient_information` for the whole side, when the bytes do not decode
or the frame is too small to hold an edge run between its corner exclusions.

## The corner/edge boundary

An edge's run is the card edge minus the first and last **84 px** — the 7 mm
corner crop `ml/corners` judges (`corner_exclusion_px` here mirrors its
`corner_size_px`). A defect inside that square belongs to the corner result;
reporting it here too would double-report one defect across two axes, and
the evaluation (#188) scores either axis against this line. Change one side
of the mirror and the other, or a defect at the seam is double-reported or
dropped.

## What v0.1.0 claims, and what it deliberately never does

The baseline reads one signal: **whitening**, exposed paper core presenting
as achromatic bright pixels inside a 1 mm band along the card edge.
Severity is banded by whitened area. `clean` is a positive claim, made only
after the printed border proved not-white — so whitening would have been
visible — and the band showed less core than the noise floor. A card whose
border itself is near-white makes the signal undiscriminating, and that
edge answers `unknown` rather than guessing.

`chipping`, `rough_cut`, `notching`, `layering` and `dent` are **never
emitted** by this baseline: chips and notches need shape analysis against
the cut line, `rough_cut` is a whole-edge texture claim (whose honest
spatial claim is "this edge" — the domain's bounding box is optional for
exactly that reason), and layering and dents are depth signals a single
normalized view does not carry. A label the analyzer cannot see is exactly
the confidently-wrong output the architecture forbids. `ml/evaluation`'s
benchmark (#188) measures what the restriction costs against the annotated
corpus, and a learned classifier enters only through that benchmark, behind
this same signature.

The V1 implementation is an OpenCV heuristic versioned by
`EDGES_VERSION` — a code constant, never a registry row. Changing any
threshold bumps it.
