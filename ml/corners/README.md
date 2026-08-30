# `ml/corners`

Classifies the four corners of one card side from the normalized artifact —
spec §14, issue #183.

`classify(data, card_frame=...)` takes the artifact's bytes and the card
rectangle the caller derives from the artifact's **stored** normalization
record (never the image boundary — #194 put a 24 px background margin around
the card), and answers `Uncertain[Mapping[CornerRegion, RegionFinding]]`:
either all four corners, each with a `CornerLabel`, a confidence, a severity
where a defect is claimed and a bounding box in artifact fractions — or
`insufficient_information` for the whole side, when the bytes do not decode
or the frame is too small to hold the corner crops.

## What v0.1.0 claims, and what it deliberately never does

The baseline reads one signal: **whitening**, exposed paper core presenting
as achromatic bright pixels inside a 1 mm band along the card edges at each
corner (the 7 mm crop is ADR 0010's contact-sheet region, judged adequate at
12 px/mm). Severity is banded by whitened area. `clean` is a positive claim,
made only after the printed border proved not-white — so whitening would have
been visible — and the band showed less core than the noise floor. A card
whose border itself is near-white makes the signal undiscriminating, and that
corner answers `unknown` rather than guessing.

`rounding`, `chipping`, `dent`, `crease` and `layering` are **never emitted**
by this baseline: none survives an honest classical heuristic at 84 px, and a
label the analyzer cannot see is exactly the confidently-wrong output the
architecture forbids. `ml/evaluation`'s benchmark (#188) measures what the
restriction costs against the annotated corpus, and a learned classifier
enters only through that benchmark, behind this same signature.

The V1 implementation is an OpenCV heuristic versioned by
`CORNERS_VERSION` — a code constant, never a registry row. Changing any
threshold bumps it.
