# `infrastructure/docker`

Dockerfiles for the web app, API, analysis worker and ML images.

The ML image needs NVIDIA Container Toolkit + CUDA for local GPU development.
The analysis worker runs with minimal privileges and restricted filesystem
access — uploaded images are untrusted input.

Populated in M0 (#20).
