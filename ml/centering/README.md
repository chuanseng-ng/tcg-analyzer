# `ml/centering`

Measures spec §21's template-aware centering from the normalized artifact —
the first of M7's four axis analyzers, issue #182.

`measure(data, card_frame=...)` takes an artifact's bytes and the card's
rectangle inside it — fractions of the unit square, derived by the caller from
the artifact's **stored** normalization record, never from the normalizer's
current thresholds (since #194 the artifact's boundary is 24 px of photographed
background away from the card, so the boundary is not the card's edge). It
returns one side's `horizontal` and `vertical` ratios plus a confidence, or
`InsufficientInformation`. `centering_of(front, back)` composes two sides into
`tcg_domain.condition.Centering`, its confidence the minimum over the measured
sides.

The arithmetic is the annotation tool's, on purpose: borders are the
midpoint-to-side distances between the found frame and the card rectangle, in
artifact pixels, and the ratios are `left / (left + right)` and
`top / (top + bottom)`, 0.5 perfect. `ml/evaluation` compares this package
against `centering_measurements`, so the two derivations must agree.

Template-aware means knowing when not to answer: a full-art, borderless or
unrecognised layout yields no frame-like quadrilateral in the accepted band
and is `insufficient_information`, never a ratio measured against a frame
that is not there. A frame that touches the card edge refuses that axis; a
frame implying an implausibly thick border refuses the side.

The V1 implementation is an OpenCV contour baseline, versioned by
`CENTERING_VERSION` as a code constant. A learned model enters only through
`ml/evaluation`'s benchmark, and must not change this signature.
