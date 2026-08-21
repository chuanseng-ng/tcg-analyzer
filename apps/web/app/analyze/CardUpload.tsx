"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { startAnalysis, uploadImage, type UploadSide } from "@/lib/api";
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
 * There is deliberately no `useRouter` here and no link onward: nothing yet
 * consumes an uploaded analysis. Confirming which card it is arrives with #104.
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
    setFailure(null);
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

      {stored ? (
        <Stored onStartOver={startOver} />
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
 * Both photographs are on the server. This is the end of what is built.
 *
 * It leads nowhere on purpose. Confirming which card this is writes to the
 * analysis (#104) and running the pipeline needs that confirmation first, so a
 * link onward from here today would be a link to a dead end.
 */
function Stored({ onStartOver }: { readonly onStartOver: () => void }) {
  const heading = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <div className={styles.done} role="status">
      <p className={styles.doneHeading} ref={heading} tabIndex={-1}>
        Both photographs are stored.
      </p>
      <p className={styles.note}>
        Nothing has been analysed yet. Confirming which card these show, checking the photographs
        are usable, reading the card&apos;s condition and the economics of grading it are still
        being built.
      </p>
      <p className={styles.note}>
        Retake either side above to replace what is stored, or start over with a different card.
      </p>
      <button className={styles.startOver} type="button" onClick={onStartOver}>
        Start over
      </button>
    </div>
  );
}
