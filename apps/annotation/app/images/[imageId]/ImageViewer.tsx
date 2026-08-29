"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  imageBytesUrl,
  readTrainingImage,
  type AnnotationImageResponse,
  type AnnotationImageSummary,
} from "@/lib/api";
import {
  classifyAnnotationFailure,
  FAILURE_MESSAGE,
  isWorthRetrying,
  type AnnotationFailure,
} from "@/lib/annotation-errors";
import {
  actualSize,
  fitted,
  pan,
  PAN_STEP,
  PAN_STEP_LARGE,
  showsRealPixels,
  transformOf,
  zoom,
  zoomAt,
  ZOOM_STEP,
  type Size,
  type View,
} from "@/lib/viewport";

import styles from "./page.module.css";

type State =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly image: AnnotationImageResponse }
  | { readonly status: "failed"; readonly failure: AnnotationFailure };

/**
 * The physical copy, with absent and null collapsed to one answer.
 *
 * `physical_copy_id` is optional *and* nullable on the wire, and both mean the
 * same thing here — ADR 0008's approved class 4 identifies no copy. Two
 * spellings of one fact is exactly the kind of thing a screen gets wrong once
 * and then keeps getting wrong, so it is answered in one place.
 */
function copyOf(view: { physical_copy_id?: string | null }): string | null {
  return view.physical_copy_id ?? null;
}

/** Prose for a `training_images.side` value. Six are possible, not two. */
function sideLabel(side: string): string {
  return side.replace(/_/g, " ");
}

/**
 * One training image, at a magnification that makes a soft corner visible.
 *
 * Two things are deliberately explicit rather than inferred. **Which
 * representation is on screen** comes from the service and is stated in the
 * badge and in the `alt` text, because a coordinate taken against a photograph
 * is not comparable with one taken against an artifact — and the next issue
 * takes coordinates. **Which other views exist** comes from the service too:
 * the toggle offers what `siblings` holds, so an image naming no physical copy
 * says so rather than offering somebody else's card.
 */
export function ImageViewer({ imageId }: { imageId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [showing, setShowing] = useState(imageId);

  useEffect(() => {
    setShowing(imageId);
  }, [imageId]);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });

    readTrainingImage(imageId, controller.signal)
      .then((image) => {
        setState({ status: "ready", image });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "failed", failure: classifyAnnotationFailure(error) });
      });

    return () => {
      controller.abort();
    };
  }, [imageId, attempt]);

  if (state.status === "loading") {
    return <p className={styles.pending}>Reading the image…</p>;
  }

  if (state.status === "failed") {
    return (
      <div className={styles.notice} role="alert">
        <p>{FAILURE_MESSAGE[state.failure]}</p>
        {isWorthRetrying(state.failure) ? (
          <button
            className={styles.secondary}
            type="button"
            onClick={() => {
              setAttempt((count) => count + 1);
            }}
          >
            Try again
          </button>
        ) : null}
        <p>
          <Link href="/">Back to the work list</Link>
        </p>
      </div>
    );
  }

  const { image } = state;
  const views: AnnotationImageSummary[] = [image, ...image.siblings];
  const current = views.find((view) => view.id === showing) ?? image;
  const onShowNext = setShowing;
  const copy = copyOf(current);

  return (
    <>
      <div className={styles.headings}>
        <h1 className={styles.heading}>{sideLabel(current.side)}</h1>
        <p className={styles.subheading}>
          {current.source.replace(/_/g, " ")}
          {/*
           * The schema makes this optional as well as nullable, so absent and
           * null are two spellings of one fact: nobody identified the copy.
           */}
          {copy === null ? " · no physical copy recorded" : ` · copy ${copy.slice(0, 8)}`}
        </p>
      </div>

      <SideToggle views={views} showing={current.id} onShow={setShowing} image={image} />

      <Frame
        view={current}
        onCycleView={() => {
          const at = views.findIndex((candidate) => candidate.id === current.id);
          const next = views[(at + 1) % views.length];
          if (next) onShowNext(next.id);
        }}
      />

      <p className={styles.footnote}>
        <Link href="/">Back to the work list</Link>
      </p>
    </>
  );
}

function SideToggle({
  views,
  showing,
  onShow,
  image,
}: {
  views: AnnotationImageSummary[];
  showing: string;
  onShow: (id: string) => void;
  image: AnnotationImageResponse;
}) {
  if (views.length === 1) {
    return (
      <p className={styles.lonely}>
        {copyOf(image) === null
          ? "This photograph is not linked to a physical copy, so it has no other side."
          : "No other view of this copy has been ingested."}
      </p>
    );
  }

  return (
    <div className={styles.sides} role="group" aria-label="Which view of the card">
      {views.map((view) => (
        <button
          key={view.id}
          type="button"
          className={view.id === showing ? styles.sideOn : styles.side}
          aria-pressed={view.id === showing}
          onClick={() => {
            onShow(view.id);
          }}
        >
          {sideLabel(view.side)}
        </button>
      ))}
    </div>
  );
}

const FALLBACK_FRAME: Size = { width: 640, height: 800 };

function Frame({ view, onCycleView }: { view: AnnotationImageSummary; onCycleView: () => void }) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState<Size>(FALLBACK_FRAME);
  const [natural, setNatural] = useState<Size | null>(null);
  const [transform, setTransform] = useState<View>({ scale: 1, x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number } | null>(null);

  const representation = view.has_artifact ? "normalized" : "original";
  const source = useMemo(() => imageBytesUrl(view.id, representation), [view.id, representation]);

  // `getBoundingClientRect` in jsdom reports zeroes, so the fallback above is
  // not defensive padding — it is what makes the transform meaningful in a test
  // and on a first paint before layout has run.
  useEffect(() => {
    const element = frameRef.current;
    if (element === null) return;

    const measure = () => {
      const box = element.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) setFrame({ width: box.width, height: box.height });
    };
    measure();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);

  const reset = useCallback(
    (size: Size) => {
      setNatural(size);
      setTransform(fitted(frame, size));
    },
    [frame],
  );

  const apply = useCallback(
    (next: (current: View, image: Size) => View) => {
      setTransform((current) => (natural === null ? current : next(current, natural)));
    },
    [natural],
  );

  /*
   * Bound here rather than with React's `onWheel`, and this is not a style
   * choice: React attaches wheel listeners passively at the root, so
   * `preventDefault()` inside `onWheel` is ignored and every zoom would also
   * scroll the page out from under the annotator.
   */
  useEffect(() => {
    const element = frameRef.current;
    if (element === null || natural === null) return;

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = element.getBoundingClientRect();
      const focus: Size = {
        width: event.clientX - box.left,
        height: event.clientY - box.top,
      };
      const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      setTransform((current) => zoomAt(current, factor, focus, frame, natural));
    };

    element.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      element.removeEventListener("wheel", onWheel);
    };
  }, [frame, natural]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const step = (event.shiftKey ? PAN_STEP_LARGE : PAN_STEP) * frame.width;
      const stepY = (event.shiftKey ? PAN_STEP_LARGE : PAN_STEP) * frame.height;

      const moves: Record<string, (current: View, image: Size) => View> = {
        ArrowLeft: (current, image) => pan(current, step, 0, frame, image),
        ArrowRight: (current, image) => pan(current, -step, 0, frame, image),
        ArrowUp: (current, image) => pan(current, 0, stepY, frame, image),
        ArrowDown: (current, image) => pan(current, 0, -stepY, frame, image),
        "+": (current, image) => zoom(current, ZOOM_STEP, frame, image),
        "=": (current, image) => zoom(current, ZOOM_STEP, frame, image),
        "-": (current, image) => zoom(current, 1 / ZOOM_STEP, frame, image),
        "0": (_current, image) => fitted(frame, image),
        "1": (current, image) => actualSize(current, frame, image),
      };

      // `f` is the front/back toggle, and it is here rather than on the buttons
      // because the issue's requirement is that toggling a side never needs the
      // mouse — an annotator's hands are already on the pan keys.
      if (event.key === "f" || event.key === "F") {
        event.preventDefault();
        onCycleView();
        return;
      }

      const move = moves[event.key];
      if (move === undefined) return;
      event.preventDefault();
      apply(move);
    },
    [apply, frame, onCycleView],
  );

  return (
    <>
      <p className={view.has_artifact ? styles.badgeArtifact : styles.badgeOriginal}>
        {view.has_artifact
          ? "Normalized artifact — coordinates are fractions of this frame."
          : "Original photograph — no card was located, so coordinates cannot be taken against it."}
      </p>

      <div
        ref={frameRef}
        className={styles.frame}
        tabIndex={0}
        role="group"
        aria-label={`${sideLabel(view.side)} — pan and zoom`}
        aria-describedby="viewer-keys"
        onKeyDown={onKeyDown}
        onPointerDown={(event) => {
          dragging.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const from = dragging.current;
          if (from === null) return;
          dragging.current = { x: event.clientX, y: event.clientY };
          apply((current, image) =>
            pan(current, event.clientX - from.x, event.clientY - from.y, frame, image),
          );
        }}
        onPointerUp={() => {
          dragging.current = null;
        }}
      >
        {/*
         * A plain <img>, not next/image: the bytes come from an API route that
         * answers `private, no-store`, and Next's optimizer would want to fetch
         * and cache a training photograph on the server. `alt` names the
         * representation, so a screen reader is told which frame this is too.
         */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className={showsRealPixels(transform) ? styles.imageExact : styles.image}
          src={source}
          alt={`${sideLabel(view.side)} of the card — ${
            view.has_artifact ? "normalized artifact" : "original photograph"
          }`}
          draggable={false}
          style={{ transform: transformOf(transform) }}
          onLoad={(event) => {
            reset({
              width: event.currentTarget.naturalWidth || FALLBACK_FRAME.width,
              height: event.currentTarget.naturalHeight || FALLBACK_FRAME.height,
            });
          }}
        />
      </div>

      <div className={styles.controls}>
        <button
          type="button"
          onClick={() => {
            apply((c, i) => zoom(c, ZOOM_STEP, frame, i));
          }}
        >
          Zoom in
        </button>
        <button
          type="button"
          onClick={() => {
            apply((c, i) => zoom(c, 1 / ZOOM_STEP, frame, i));
          }}
        >
          Zoom out
        </button>
        <button
          type="button"
          onClick={() => {
            apply((_c, i) => fitted(frame, i));
          }}
        >
          Fit
        </button>
        <button
          type="button"
          onClick={() => {
            apply((c, i) => actualSize(c, frame, i));
          }}
        >
          Actual size
        </button>
        <output className={styles.magnification}>
          {`${String(Math.round(transform.scale * 100))}%`}
        </output>
      </div>

      {/*
       * Visible, not a tooltip. It is the accessibility affordance the frame's
       * `aria-describedby` points at *and* the only way anybody discovers the
       * shortcuts — and this is a tool somebody uses for hours.
       */}
      <p className={styles.keys} id="viewer-keys">
        <kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd> pan (hold <kbd>Shift</kbd> to go
        further) · <kbd>+</kbd> <kbd>−</kbd> zoom · <kbd>0</kbd> fit · <kbd>1</kbd> actual size ·{" "}
        <kbd>f</kbd> other side
      </p>
    </>
  );
}
