# `services/analysis`

Orchestrates the analysis pipeline: image-quality gate, card detection,
perspective correction, condition representation, then the per-company grading
predictors.

Long-running inference runs as a background job and never blocks an HTTP
request. Uploaded images are untrusted input — this worker runs isolated, with
minimal privileges, and never executes files from upload directories.

Populated in M2.
