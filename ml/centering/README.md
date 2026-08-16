# `ml/centering`

Measures border ratios and produces a centering assessment.

Borderless and full-art layouts have no symmetric border to measure — detect the
frame type and return `insufficient_information` rather than a meaningless
ratio.

Populated in M7.
