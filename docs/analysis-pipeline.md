# The analysis pipeline

The stages between an uploaded photograph and a card the rest of the system can
measure. [`architecture.md`](architecture.md) has the pipeline's shape and the
invariants that constrain it; this file has what each stage actually does, and
the decisions in each that are easy to undo by accident.

Each stage is a package under `ml/`, reached through the `worker` uv extra —
which is why the worker has an image of its own and the API does not carry
OpenCV. See [`development.md`](development.md#background-jobs).

## The image-quality gate

Spec §18 puts a quality gate between file validation and card detection, and
spec §19 fixes what it may conclude: `unusable` stops the analysis, `poor`
continues but the user must be told. The M2 implementation is OpenCV heuristics
in `ml/image-quality`, and M7 replaces it with a model behind the same
signature.

Five of §19's eleven conditions are measured from the frame alone — blur, low
resolution, poor exposure, excessive darkness, excessive brightness. The other
six need the card boundary the detector below supplies. Five of those are
questions about the boundary itself; **glare is the sixth**, and it is a
measurement of pixels like the five above it, but of the card's pixels —
reflection is a property of the card's surface, and measured over the whole
frame it is diluted by whatever the card is lying on. Without a boundary all six
are reported **undetermined with a reason** rather than guessed, and a
photograph with six conditions unchecked cannot be `good` however sharp it is.

**What such a photograph *is* depends on why the card is missing**, and the two
answers are not the same (#319). A detector that ran and found nothing has not
merely left six questions open — it could not assess the photograph at all, so
the report carries a `refusal` of `no_card_found` and the verdict is
`unusable`: spec §19's "analysis should stop", and the one failure the person
holding the phone can act on. A gate asked to judge a frame with no detector
run against it — which the corpus normalization pass does, and the API pipeline
never does — stops at `acceptable`, "nothing wrong found, and something not
looked at". In practice that means **every analysis through this pipeline is
judged on all eleven conditions or refused**, because detection always runs.

Every verdict is persisted on `images` — the status, a `[0, 1]` score and all
eleven findings — and served by `GET /analyses/{id}`, which is what lets
`/analyze` say what was wrong before it hands off to the catalog. The thresholds
that produced a verdict are recorded beside it, along with the versions of the
gate and of the detector, so a later model can be compared against the baseline
that actually ran.

## Card boundary detection

`ml/card-detection` locates the card so that everything downstream operates on
the card rather than on the table it is lying on (spec §18). It takes the stored
bytes and returns four corners **clockwise from the top left**, in the original
photograph's coordinates, with a detection confidence — or an explicit "no card
found", never a guessed quadrilateral. The V1 implementation is an OpenCV
contour baseline; a learned detector is an M7 option behind the same signature.

Three things about it are deliberate and easy to undo by accident:

- **The corner order is validated, not documented.** Perspective correction
  reads the four corners positionally, so a wrong order does not fail — it
  silently rotates or mirrors the card. `CardGeometry` refuses a quadrilateral
  that does not run clockwise around a convex shape, so a mirrored one is not
  representable.
- **The boundary is not cropped tight.** M7's edge and corner analysis needs the
  card's actual edge, and a tight crop shaves the whitening that matters most.
- **Concentric quadrilaterals are one card.** A sleeve, a top-loader and the two
  walls of an edge ribbon all put a second quadrilateral around the first;
  counting those as two cards would refuse the photograph for `multiple_cards`.
  The spread between them is what answers sleeve obstruction instead — the
  weakest heuristic in the pipeline, and one that costs a `poor` rather than a
  refusal.

## Perspective correction and normalization

`ml/normalization` warps the detected quadrilateral into the standardized
artifact every later stage reads — spec §18, and M2's acceptance criterion. It
is an **804 x 1104 PNG**: the card at 12 pixels per millimetre of a
63 x 88 mm card — an inner 756 x 1056 rectangle, exactly a real card's
proportions with no rounding — inside a 2 mm margin of the photograph around
it (#194), so an edge can be judged against the background it was shot on. A
centering ratio is measured against the card's inner rectangle, whose place
the annotation service reports beside each image. The transform that produced it is
persisted alongside, because spec §51's post-V1 defect visualisation draws boxes
on the *original* photograph and that mapping is not recoverable afterwards.

Four things about it are deliberate:

- **Nothing is enhanced.** No sharpening, no denoising, no contrast stretching,
  no white balance. Every stage downstream exists to measure scratches,
  whitening and print lines; a denoised scratch is one the model cannot see and
  a sharpened edge is whitening that was never there.
- **The resampling is two steps.** `warpPerspective` has no area filter, so
  warping a 4000-pixel card straight down to 1056 point-samples it, and the
  moire that comes back is fabricated surface texture. The warp goes to an
  integer multiple of the output instead and one box filter takes it down.
- **The artifact is aspect-normalized, not upright.** The detector anchors its
  traversal at the corner nearest the frame origin, so a card photographed on
  its side is rotated a quarter turn to put its short edge first — which fixes
  the proportions. Which of two rotations puts the printed top at the top needs
  the artwork read, and that is card identification's question. The quarter turn
  applied is recorded.
- **No card located means no artifact.** `normalized_uri` stays NULL rather than
  holding a resized whole frame, which would be a standardized artifact of the
  table the card was lying on. The gate refuses such a photograph outright
  (#319), so the analysis stops before anything would have measured it. The
  original is always kept unmodified.

## Bounding a run

Every stage above decodes an untrusted photograph, and a stage that never
returns holds a worker slot nobody else can use. Two limits bound one run, and
their numbers come from the analysis-latency budget in
[`observability.md`](observability.md) rather than from taste — a change to
either is a change to that document in the same pull request.

- **The soft limit, 60 s**, is six times the budget and past both waits the
  product already makes (`/analyze` polls for 20 s, CI allows 30 s). Celery
  raises `SoftTimeLimitExceeded` *inside* the task, so the runner catches it
  like any other failure and the analysis is recorded `failed` / `timed_out`.
- **The hard limit, 120 s**, is the backstop. A soft limit is a signal and a
  signal cannot interrupt a C call, so an OpenCV kernel that never returns would
  ignore it; this one kills the child. With late acknowledgement and the prefork
  pool that **acks the message**, which is the whole difficulty below.

A prefork child is also replaced after a hundred runs. Each run decodes several
megapixels, and glibc does not return freed arenas to the operating system, so
a long-lived child's resident size is the high-water mark of everything it ever
did rather than of what it is doing.

**A failure is retried only if a second attempt could go differently.** A soft
limit exceeded, a grading model that raised, and any `ValueError` — an invalid
condition assessment, an image whose stored bytes do not decode — are facts
about this analysis, and four attempts of them are four times the same answer;
they are recorded on the first. An outage is the opposite, so a store that could
not be read, a socket that closed and the driver's own errors keep the jittered
backoff and then the dead-letter record.

**One failure has no exception to key on**, and it is the hard limit's: the
child is gone, the message is acked, and there is nothing left to catch. A run
is a single transaction — the claim, spec §57's record, the verdicts, the
condition and the grades all commit at the end — so a killed run rolls back
completely and leaves the analysis at `uploaded` with nothing written. An hourly
sweep (`tcg_api.analysis.stalls`, beside §54's retention sweep) fails every
analysis still `uploaded` **fifteen minutes after its last photograph arrived**,
recording `stalled`. Fifteen minutes because the longest legitimate run is four
attempts of the hard limit and three backoffs, eleven; the photographs are the
clock because `/analyze` runs the analysis in the same click that uploads the
second side, and the run itself writes nothing until it commits. A run still
going is safe from it regardless: the claim holds the row lock, so the sweep's
own conditional `UPDATE` waits and then finds a state it may not move from.

A swept analysis therefore looks exactly like any other run that raised — an
empty spec §57 record, no verdicts, no condition document — with `failed` and
its reason on the row. It is terminal: photographing the card again is what
starts over, and there is no re-run route.
