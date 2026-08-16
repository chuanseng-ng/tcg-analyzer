# `ml/evaluation`

Benchmark and calibration harness for every model in `ml/`.

**Calibration is mandatory, not optional.** A model claiming "PSA 10 = 80%" must
be right about 80% of the time. Brier score, expected calibration error and
calibration curves live here.

Populated in M8.
