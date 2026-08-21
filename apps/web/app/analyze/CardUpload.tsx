"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { forgetAnalysis, rememberAnalysis } from "@/lib/analysis-session";
import {
  ApiError,
  readAnalysis,
  runAnalysis,
  startAnalysis,
  uploadImage,
  type AnalysisResponse,
  type UploadSide,
} from "@/lib/api";
import { concerning, faultsIn, isUnusable, nameOf } from "@/lib/quality-copy";
import { classifyUploadFailure, type UploadFailure } from "@/lib/upload-errors";
import {
  ACCEPT_ATTRIBUTE,
  EMPTY_SLOTS,
  SIDES,
  previewIn,
  rejectionOf,
  type Slot,
  type Slots,
} from "@/lib/upload-slots";

import styles from "./CardUpload.module.css";

/**
 * The states an analysis passes through while the quality gate is running.
 *
 * `uploaded` is where `run` leaves it and `identifying` is where the worker
 * claims it; anything else means the gate has spoken, one way or the other.
 */
const STILL_WORKING: ReadonlySet<string> = new Set(["uploaded", "identifying"]);

/** Between polls. Long enough not to hammer, short enough to feel immediate. */
const VERDICT_POLL_INTERVAL_MS = 1_000;

/**
 * How many polls before the screen gives up waiting.
 *
 * Twenty, so roughly twenty seconds. Giving up is not an error: the gate has
 * simply not got there, and going on to the catalog is right — `/identify`'s
 * 409 then genuinely means "not ready yet", which is the one thing it can say
 * honestly at that point.
 */
const VERDICT_POLL_ATTEMPTS = 20;

/**
 * What the gate concluded, once it has.
 *
 * `refused` is spec §19's `unusable`: the analysis stopped and there is nothing
 * to go on to. `warned` is `poor`, which §19 says continues — "but the user must
 * be informed", which is why it is a state of this screen and not a banner on
 * the way past. Both carry the photographs worth mentioning, worst first.
 */
type Verdict =
  | { readonly kind: "refused"; readonly images: readonly ImageQuality[] }
  | { readonly kind: "warned"; readonly images: readonly ImageQuality[] };

type ImageQuality = AnalysisResponse["images"][number];

/**
 * How each side is described, everywhere it is described.
 *
 * Getting the two mixed up silently corrupts centering and condition analysis —
 * the pipeline has no way to notice — so the side is named in the heading, the
 * button, the alt text and the status line. Never by position or colour alone.
 */
const SIDE_COPY: Readonly<Record<UploadSide, { title: string; hint: string; alt: string }>> = {
  front: {
    title: "Front of the card",
    hint: "The side with the artwork, filling as much of the frame as you can.",
    alt: "The photograph you chose for the front of your card",
  },
  back: {
    title: "Back of the card",
    hint: "The printed reverse. Turn the card over rather than flipping the phone.",
    alt: "The photograph you chose for the back of your card",
  },
};

/**
 * Photograph both sides of a card and commit them to an analysis — spec §48,
 * issue #34.
 *
 * **Nothing is sent until the user says so.** Both photographs are staged in
 * the browser and only uploaded by the explicit action at the bottom. That is
 * what makes Remove possible at all: spec §65's state graph is forward-only, so
 * once an analysis holds an image there is no legal move that takes it back —
 * a photograph that has not been sent is the only one that can be un-chosen.
 * After the upload the correction is a Retake, which the endpoint treats as a
 * replacement, and Start over, which abandons the analysis for a fresh one.
 *
 * **The upload can fail halfway, and the screen says so.** Front and back are
 * two requests and either can fail alone. Each slot reports its own state, and
 * trying again re-sends only the side that is not stored — and never opens a
 * second analysis, because the identifier is kept.
 *
 * **Where it leads.** Once both photographs are stored the screen hands off to
 * the catalog: the analysis is run (spec §65) and the identifier is kept in
 * `sessionStorage`, so `/identify` can record the card the user picks against
 * it (#104). That is the only navigation this component performs, and it
 * happens on an explicit tap.
 */
export function CardUpload() {
  const [slots, setSlots] = useState<Slots>(EMPTY_SLOTS);
  const [rejections, setRejections] = useState<Readonly<Record<UploadSide, string | null>>>({
    front: null,
    back: null,
  });
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<UploadFailure | null>(null);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const router = useRouter();

  // Every object URL this component has minted. Revoked on replacement, on
  // removal, and on unmount — a preview that outlives its slot holds the whole
  // photograph in memory, and there are two of them at up to 15 MB each.
  const previews = useRef(new Set<string>());

  useEffect(() => {
    const urls = previews.current;
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  // The §55 rate limiter answers with `Retry-After` and nothing else useful, so
  // the wait is counted down rather than turned into a button that would fire
  // straight back into the limit (ADR 0005).
  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  const dropPreview = useCallback((slot: Slot) => {
    const url = previewIn(slot);
    if (url !== null && previews.current.delete(url)) {
      URL.revokeObjectURL(url);
    }
  }, []);

  const choose = useCallback(
    (side: UploadSide, file: File | undefined) => {
      if (file === undefined) return;

      const rejection = rejectionOf(file);
      setRejections((current) => ({ ...current, [side]: rejection }));
      if (rejection !== null) return;

      const previewUrl = URL.createObjectURL(file);
      previews.current.add(previewUrl);
      setFailure(null);
      // A new photograph for this side means the old verdict is about bytes the
      // analysis is no longer going to hold.
      setVerdict(null);
      setSlots((current) => {
        dropPreview(current[side]);
        return { ...current, [side]: { status: "staged", file, previewUrl } };
      });
    },
    [dropPreview],
  );

  const remove = useCallback(
    (side: UploadSide) => {
      setRejections((current) => ({ ...current, [side]: null }));
      setFailure(null);
      setVerdict(null);
      setSlots((current) => {
        dropPreview(current[side]);
        return { ...current, [side]: { status: "empty" } };
      });
    },
    [dropPreview],
  );

  const startOver = useCallback(() => {
    setSlots((current) => {
      for (const side of SIDES) dropPreview(current[side]);
      return EMPTY_SLOTS;
    });
    setRejections({ front: null, back: null });
    // The abandoned analysis is left where it is. It expires with the session
    // and #41's retention sweep deletes its images; there is no endpoint that
    // removes one, and inventing a request here would not make that true.
    setAnalysisId(null);
    forgetAnalysis();
    setFailure(null);
    setVerdict(null);
  }, [dropPreview]);

  const send = useCallback(async () => {
    // Nothing can change `slots` while this runs: every control is disabled
    // for the duration, which is why the closure's copy is safe to read.
    const pending = SIDES.filter((side) => slots[side].status === "staged");
    if (pending.length === 0) return;

    setBusy(true);
    setFailure(null);

    try {
      const id = analysisId ?? (await startAnalysis()).id;
      setAnalysisId(id);
      // Kept where `/identify` can find it after the user has been through the
      // catalog. The API scopes the analysis to the session cookie, so this is
      // a convenience rather than anything that grants access.
      rememberAnalysis(id);

      for (const side of pending) {
        const slot = slots[side];
        if (slot.status !== "staged") continue;

        const { file, previewUrl } = slot;
        setSlots((current) => ({
          ...current,
          [side]: { status: "uploading", file, previewUrl, progress: 0 },
        }));

        const image = await uploadImage({
          analysisId: id,
          side,
          file,
          onProgress: (fraction) =>
            setSlots((current) =>
              current[side].status === "uploading"
                ? {
                    ...current,
                    [side]: { status: "uploading", file, previewUrl, progress: fraction },
                  }
                : current,
            ),
        });

        setSlots((current) => ({
          ...current,
          [side]: { status: "stored", file, previewUrl, image },
        }));
      }
    } catch (error: unknown) {
      const classified = classifyUploadFailure(error);
      setFailure(classified);
      if (classified.retryAfterSeconds !== undefined) {
        setWaitSeconds(classified.retryAfterSeconds);
      }
      // Whatever was in flight keeps its photograph and goes back to staged, so
      // trying again re-sends exactly the sides that are not stored.
      setSlots((current) => ({ front: reverted(current.front), back: reverted(current.back) }));
    } finally {
      setBusy(false);
    }
  }, [analysisId, slots]);

  const chooseCard = useCallback(async () => {
    if (analysisId === null) return;

    setBusy(true);
    setFailure(null);
    setVerdict(null);
    try {
      await runAnalysis(analysisId);
    } catch (error: unknown) {
      // A 409 means the analysis is already past `uploaded` — it has been run
      // before, which is exactly where this button was trying to get it. Going
      // on is the right answer, not an error.
      if (!(error instanceof ApiError) || error.status !== 409) {
        const classified = classifyUploadFailure(error);
        setFailure(classified);
        if (classified.retryAfterSeconds !== undefined) {
          setWaitSeconds(classified.retryAfterSeconds);
        }
        setBusy(false);
        return;
      }
    }

    // Wait for spec §19's gate before handing off. This screen is the only one
    // that can act on what it finds: the retake is here, and `/identify` is
    // forbidden a route back (#91). #34 deliberately had no polling anywhere,
    // and this is not a retry loop — it is waiting for an answer, on the
    // endpoint §65 says a client polls.
    const analysis = await settled(analysisId);
    if (analysis === null) {
      // The gate has not got there. Going on is honest: `/identify` will say
      // the photographs are not ready, which is exactly what is true.
      router.push("/cards");
      return;
    }

    const worrying = concerning(analysis.images);
    if (analysis.status === "failed") {
      setVerdict({ kind: "refused", images: worrying });
      setBusy(false);
      return;
    }
    if (worrying.length > 0) {
      // §19: "analysis may continue but the user must be informed". Informed
      // *here*, with the retake in reach, rather than by a banner they navigate
      // past — and it takes a tap to go on, so it cannot be missed.
      setVerdict({ kind: "warned", images: worrying });
      setBusy(false);
      return;
    }

    router.push("/cards");
  }, [analysisId, router]);

  const ready = SIDES.every((side) => slots[side].status !== "empty");
  const stored = SIDES.every((side) => slots[side].status === "stored");
  const started = analysisId !== null;

  return (
    <div className={styles.upload}>
      <h1 className={styles.heading}>Photograph both sides of your card</h1>
      <p className={styles.lede}>
        An ordinary photograph in ordinary light is what this is built for — no lightbox and no
        tripod. Nothing is sent until you choose to send it.
      </p>

      <div className={styles.slots}>
        {SIDES.map((side) => (
          <SlotPanel
            key={side}
            side={side}
            slot={slots[side]}
            rejection={rejections[side]}
            disabled={busy}
            onChoose={choose}
            onRemove={remove}
          />
        ))}
      </div>

      {failure !== null && <Failure failure={failure} waitSeconds={waitSeconds} />}

      {verdict !== null && (
        <QualityVerdict verdict={verdict} onContinue={() => router.push("/cards")} />
      )}

      {stored ? (
        <Stored
          busy={busy}
          judged={verdict !== null}
          onChooseCard={() => void chooseCard()}
          onStartOver={startOver}
        />
      ) : (
        <div className={styles.actions}>
          <button
            className={styles.send}
            type="button"
            onClick={() => void send()}
            disabled={!ready || busy || waitSeconds > 0}
          >
            {busy ? "Sending…" : started ? "Send the rest" : "Use these photographs"}
          </button>
          {!ready && (
            <p className={styles.note}>Both sides are needed before anything can be sent.</p>
          )}
          {started && (
            <button className={styles.startOver} type="button" onClick={startOver} disabled={busy}>
              Start over
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Poll until the quality gate has spoken, or give up.
 *
 * Polls first and sleeps second, so an analysis that has already been judged
 * costs no delay at all. A read that fails is treated as "no answer" rather than
 * as an error: nothing has gone wrong with the user's photographs, and the
 * screen has somewhere sensible to go either way.
 */
async function settled(analysisId: string): Promise<AnalysisResponse | null> {
  for (let attempt = 0; attempt < VERDICT_POLL_ATTEMPTS; attempt += 1) {
    try {
      const analysis = await readAnalysis(analysisId);
      if (!STILL_WORKING.has(analysis.status)) return analysis;
    } catch {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, VERDICT_POLL_INTERVAL_MS));
  }
  return null;
}

/** An upload that did not finish keeps its photograph, ready to be sent again. */
function reverted(slot: Slot): Slot {
  return slot.status === "uploading"
    ? { status: "staged", file: slot.file, previewUrl: slot.previewUrl }
    : slot;
}

function SlotPanel({
  side,
  slot,
  rejection,
  disabled,
  onChoose,
  onRemove,
}: {
  readonly side: UploadSide;
  readonly slot: Slot;
  readonly rejection: string | null;
  readonly disabled: boolean;
  readonly onChoose: (side: UploadSide, file: File | undefined) => void;
  readonly onRemove: (side: UploadSide) => void;
}) {
  const copy = SIDE_COPY[side];
  const headingId = `photograph-${side}-heading`;
  const preview = previewIn(slot);

  return (
    <section className={styles.slot} aria-labelledby={headingId}>
      <h2 className={styles.slotHeading} id={headingId}>
        {copy.title}
      </h2>

      <div className={styles.preview} data-filled={preview !== null}>
        {preview === null ? (
          <p className={styles.placeholder}>No photograph yet</p>
        ) : (
          /* `next/image` optimises URLs it can fetch. This is a `blob:` URL for
             a file that never left the browser, so there is nothing to optimise
             and nothing a loader could reach. */
          // eslint-disable-next-line @next/next/no-img-element
          <img className={styles.previewImage} src={preview} alt={copy.alt} />
        )}
      </div>

      <p className={styles.hint}>{copy.hint}</p>

      <div className={styles.slotActions}>
        {/*
         * One native file input per side, and no `capture` attribute.
         * `capture` forces the camera and *removes* the photo library and file
         * picker; without it mobile Safari and Chrome offer Take Photo, Photo
         * Library and Browse from this one control, and a desktop gets the file
         * picker.
         *
         * The input is nested inside its label and laid over it transparently
         * rather than hidden: `display: none` and `visibility: hidden` both take
         * it out of the tab order, leaving a mouse-only control. Focus lands on
         * the input and the label draws the ring with `:focus-within`.
         */}
        <label className={styles.choose} data-disabled={disabled}>
          <span>{slot.status === "empty" ? `Add the ${side}` : `Retake the ${side}`}</span>
          <input
            className={styles.file}
            type="file"
            accept={ACCEPT_ATTRIBUTE}
            disabled={disabled}
            onChange={(event) => onChoose(side, event.target.files?.[0])}
          />
        </label>
        {slot.status === "staged" && (
          <button
            className={styles.remove}
            type="button"
            disabled={disabled}
            onClick={() => onRemove(side)}
          >
            Remove the {side}
          </button>
        )}
      </div>

      {rejection !== null && (
        <p className={styles.rejection} role="alert">
          {rejection}
        </p>
      )}

      <SlotStatus side={side} slot={slot} />
    </section>
  );
}

function SlotStatus({ side, slot }: { readonly side: UploadSide; readonly slot: Slot }) {
  if (slot.status === "uploading") {
    const percent = slot.progress === null ? null : Math.round(slot.progress * 100);
    return (
      <p className={styles.state} role="status" aria-live="polite">
        {/* A native <progress>, indeterminate when the browser cannot say. */}
        <progress
          className={styles.progress}
          max={1}
          {...(slot.progress === null ? {} : { value: slot.progress })}
        />
        <span>
          {percent === null
            ? `Sending the ${side}…`
            : `Sending the ${side} — ${String(percent)}% sent`}
        </span>
      </p>
    );
  }

  return (
    <p className={styles.state} role="status" aria-live="polite">
      {slot.status === "stored"
        ? `The ${side} is stored.`
        : slot.status === "staged"
          ? `Ready to send. Nothing has left this device yet.`
          : `Nothing chosen yet.`}
    </p>
  );
}

function Failure({
  failure,
  waitSeconds,
}: {
  readonly failure: UploadFailure;
  readonly waitSeconds: number;
}) {
  return (
    <div className={styles.failure} role="alert">
      <p className={styles.failureMessage}>{failure.message}</p>
      <p className={styles.failureNote}>
        {failure.action === "wait"
          ? waitSeconds > 0
            ? `Sending is paused for ${String(waitSeconds)} more second${waitSeconds === 1 ? "" : "s"}. Your photographs are still here.`
            : "You can send again now. Your photographs are still here."
          : failure.action === "restart"
            ? "Start over below to begin a new analysis. You will need to choose both photographs again."
            : failure.action === "retake"
              ? "Choose a different photograph for that side and send again."
              : "Your photographs are still here — sending again is safe."}
      </p>
    </div>
  );
}

/**
 * What the quality gate found — spec §19.
 *
 * Two outcomes and one component, because they say almost the same thing and
 * differ in exactly one way: whether there is a way on. `unusable` stopped the
 * analysis and offers nothing but a retake; `poor` went through and needs a
 * deliberate tap, so that "the user must be informed" means informed rather
 * than shown something on the way past.
 *
 * The retake controls are the ones already above — this panel does not
 * duplicate them, it points at them. A second set of file inputs would be a
 * second place for a side to be replaced, and they would disagree.
 */
function QualityVerdict({
  verdict,
  onContinue,
}: {
  readonly verdict: Verdict;
  readonly onContinue: () => void;
}) {
  const heading = useRef<HTMLParagraphElement>(null);
  const refused = verdict.kind === "refused";

  useEffect(() => {
    heading.current?.focus();
  }, [verdict]);

  return (
    <div className={styles.verdict} data-refused={refused} role="alert">
      <p className={styles.verdictHeading} ref={heading} tabIndex={-1}>
        {refused
          ? "These photographs cannot be analysed."
          : "These photographs will do, but something is off."}
      </p>

      <ul className={styles.verdictList}>
        {verdict.images.map((image) => (
          <li key={image.side} className={styles.verdictItem}>
            <span className={styles.verdictSide}>
              The {nameOf(image.side)}
              {isUnusable(image) ? "" : " — usable, but"}:
            </span>{" "}
            {faultsIn(image).join(" ")}
          </li>
        ))}
      </ul>

      <p className={styles.note}>
        {refused
          ? "Retake the side above and send it again. Nothing else has to be done over."
          : "Going on is fine — the reading may just be less reliable. Retaking above is the alternative."}
      </p>

      {!refused && (
        <button className={styles.send} type="button" onClick={onContinue}>
          Use them anyway
        </button>
      )}
    </div>
  );
}

/**
 * Both photographs are on the server, and the next thing the product needs is
 * the one thing it cannot work out for itself: which card this is.
 *
 * The button runs the analysis (spec §65) and goes to the catalog. Nothing
 * navigates on its own — it takes this tap — and the analysis is not confirmed
 * here: that is `/identify`'s job, and it is a separate, deliberate answer to a
 * question this screen never asks.
 *
 * **Once the gate has spoken, this panel gets out of the way.** The verdict
 * owns the forward action from then on — it refuses one outright, or offers
 * "Use them anyway" — and leaving a second, cheerier route to the same place
 * would be redundant beneath a warning and a plain contradiction beneath a
 * refusal. What survives is Start over, which is true either way.
 */
function Stored({
  busy,
  judged,
  onChooseCard,
  onStartOver,
}: {
  readonly busy: boolean;
  readonly judged: boolean;
  readonly onChooseCard: () => void;
  readonly onStartOver: () => void;
}) {
  const heading = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <div className={styles.done} role="status" data-judged={judged}>
      {!judged && (
        <>
          <p className={styles.doneHeading} ref={heading} tabIndex={-1}>
            Both photographs are stored.
          </p>
          <p className={styles.note}>
            Nothing has been analysed yet. Next, find this card in the catalog and confirm it — the
            product will not guess which card you are holding. Reading the card&apos;s condition and
            the economics of grading it are still being built.
          </p>
          <button className={styles.send} type="button" onClick={onChooseCard} disabled={busy}>
            {busy ? "Getting ready…" : "Choose which card this is"}
          </button>
        </>
      )}
      <p className={styles.note}>
        Retake either side above to replace what is stored, or start over with a different card.
      </p>
      <button className={styles.startOver} type="button" onClick={onStartOver} disabled={busy}>
        Start over
      </button>
    </div>
  );
}
