# `ml/card-detection`

Locates the card boundary within an uploaded photo so downstream stages operate
on the card rather than the background — spec §18, issue #37.

`detect(data)` takes the stored image bytes and returns a
`tcg_domain.card_geometry.CardGeometry` — four corners clockwise from the top
left, in the original photograph's coordinates, with a detection confidence — or
`INSUFFICIENT_INFORMATION` when no card-like quadrilateral is there. It never
guesses a quadrilateral: a failure degrades into the image-quality gate, which
reports the five conditions that need the card located as `undetermined`.

The V1 implementation is an OpenCV contour baseline. A learned detector is an M7
option if it proves insufficient, and must not change this signature.
