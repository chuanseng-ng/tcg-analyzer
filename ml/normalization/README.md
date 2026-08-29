# `ml/normalization`

Turns a photograph plus the card's quadrilateral into the standardized artifact
every later stage reads — spec §18, issue #38.

`normalize(data, geometry)` takes the stored image bytes and a
`tcg_domain.card_geometry.CardGeometry`, and returns a `Normalized`: a
756 x 1056 PNG, the 3x3 transform that produced it, and the quarter-turn
applied. 756 x 1056 is 12 px/mm, so the output is exactly the 63:88 proportions
of a real card and centering ratios measured on it mean what they say. That
resolution was challenged and upheld against real photographs in
[ADR 0010](../../docs/adr/0010-what-surface-defects-are-measured-against.md):
corners and centering are adequately sampled, and §16's fine surface classes
are unresolvable at any rate a source photograph supports — they are
`insufficient_information` against this artifact, not a reason to raise the
number.

**Nothing here enhances the photograph.** No sharpening, no denoising, no
contrast stretching: the stages downstream exist to measure scratches,
whitening and print lines, and enhancement fabricates or erases exactly those.
The only signal processing is the resampling the warp cannot avoid, which is
done in two steps to keep it from aliasing.

The transform is returned so that a coordinate in the normalized artifact can
be mapped back to the original photograph, which is what spec §51's post-V1
defect visualisation needs.
