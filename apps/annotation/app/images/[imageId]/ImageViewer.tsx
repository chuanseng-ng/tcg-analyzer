"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  hasWork,
  requestBodyFrom,
  WHOLE_ARTIFACT,
  type BoundingBox,
  type CenteringRequest,
  type MarkerDraft,
  type MarkerRequest,
} from "@/lib/annotations";
import {
  imageBytesUrl,
  listImagesAwaitingAnnotation,
  readTrainingImage,
  saveAnnotations,
  type AnnotationImageResponse,
  type AnnotationImageSummary,
  type Representation,
  type StoredMarker,
} from "@/lib/api";
import {
  classifyAnnotationFailure,
  classifySaveFailure,
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

import {
  CaptureLayer,
  CenteringControls,
  DraftList,
  MARKER_TOOLS,
  MarkerControls,
  Overlay,
  SaveBar,
  TOOL_KEYS,
  type Tool,
} from "./Annotator";
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

/** A local identifier for a staged marker, so it can be taken off again. */
function draftId(): string {
  return `${String(Date.now())}-${Math.random().toString(36).slice(2)}`;
}

/**
 * One training image, at a magnification that makes a soft corner visible, and
 * the controls that turn what is visible into a label.
 *
 * Two things are deliberately explicit rather than inferred. **Which
 * representation is on screen** comes from the service and is stated in the
 * badge and in the `alt` text, because a coordinate taken against a photograph
 * is not comparable with one taken against an artifact. **Which other views
 * exist** comes from the service too: the toggle offers what `siblings` holds,
 * so an image naming no physical copy says so rather than offering somebody
 * else's card.
 *
 * **Drafts belong to the view on screen.** The toggle changes which image the
 * frame is showing without navigating, and a marker belongs to the image whose
 * artifact its coordinates are fractions of — `training_images.side` is what
 * says which face that is, and #158 refuses a `side` column on an annotation for
 * the same reason. So the toggle asks before it moves, and one Save writes one
 * image.
 */
export function ImageViewer({ imageId }: { imageId: string }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [showing, setShowing] = useState(imageId);

  const [tool, setTool] = useState<Tool>("pan");
  const [shown, setShown] = useState<Representation>("normalized");
  const [pending, setPending] = useState<BoundingBox | null>(null);
  const [drafts, setDrafts] = useState<readonly MarkerDraft[]>([]);
  const [centering, setCentering] = useState<CenteringRequest | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveFailure, setSaveFailure] = useState<AnnotationFailure | null>(null);

  const staged = hasWork(drafts, centering);

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

  /*
   * `beforeunload` covers the tab closing and a hard reload, and nothing else —
   * it does not fire on `router.push` or a `<Link>`. The two in-app exits are
   * guarded where they are: the side toggle and the link back to the work list.
   */
  useEffect(() => {
    if (!staged) return;

    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [staged]);

  const discardStaged = useCallback(() => {
    setDrafts([]);
    setCentering(null);
    setPending(null);
    setTool("pan");
    setShown("normalized");
    setSaveFailure(null);
  }, []);

  /** Confirm before an action that would strand staged work. */
  const mayLeave = useCallback(() => {
    if (!staged) return true;
    return window.confirm(
      "You have annotations that have not been saved. Leaving this view discards them.",
    );
  }, [staged]);

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
  const copy = copyOf(current);
  // The stored markers are the detail's, and the detail is the route's image —
  // a sibling's are read when the annotator navigates to it.
  const stored: StoredMarker[] = current.id === image.id ? image.annotations : [];

  // The frame on screen. An image with no artifact has only its photograph;
  // one with an artifact starts there and can be toggled to the original —
  // ADR 0010's one route to §16's fine defect classes (#175).
  const displayed: Representation = current.has_artifact ? shown : "original";

  /*
   * On the original view of an artifact-bearing image only pan and the surface
   * tool arm. This is not polish: a corner or edge box taken against the
   * photograph would be stored as a fraction of the artifact, and
   * `centeringFrom` assumes the artifact's edges are the card's edges — true
   * only on the normalized frame. An image with *no* artifact keeps every tool
   * armable, exactly as before #175: a boxless corner is still a true thing to
   * say about a photograph, and the save bar warns about the rest.
   */
  const armable = (candidate: Tool): boolean =>
    !current.has_artifact ||
    displayed === "normalized" ||
    candidate === "pan" ||
    candidate === "surface";

  // One path for the buttons and the keyboard, so neither can arm a tool the
  // frame on screen cannot honestly serve.
  const arm = (candidate: Tool) => {
    if (!armable(candidate)) return;
    setTool(candidate);
    setPending(null);
  };

  const toggleRepresentation = () => {
    if (!current.has_artifact) return;
    const next: Representation = displayed === "normalized" ? "original" : "normalized";
    setShown(next);
    // A placed box's fractions belong to the frame it was drawn on; drafts are
    // kept — same image — and the overlay shows each against its own frame.
    setPending(null);
    if (next === "original" && tool !== "pan" && tool !== "surface") setTool("pan");
  };

  const show = (id: string) => {
    if (id === showing) return;
    if (!mayLeave()) return;
    discardStaged();
    setShowing(id);
  };

  /**
   * Where to go once this image is written.
   *
   * A sibling first, when there is one nobody has annotated: the front and the
   * back of one card are two rows ordered by ingestion time, and they need not be
   * adjacent in the queue — sending the annotator away from a card they have half
   * finished is the wrong answer to "annotate an image, front and back".
   *
   * Otherwise the head of the queue. That is the *oldest* image awaiting
   * annotation rather than the next row of whatever page they came from, which is
   * what a queue should do — and it sidesteps the paging skew a set that shrinks
   * as it is read would otherwise cause.
   */
  const advance = async (justSaved: string) => {
    const sibling = views.find((view) => view.id !== justSaved && view.id === image.id);
    if (sibling !== undefined && image.annotations.length === 0 && image.centering.length === 0) {
      setShowing(sibling.id);
      router.push(`/images/${sibling.id}`);
      return;
    }

    try {
      const next = await listImagesAwaitingAnnotation({ limit: 1, offset: 0 });
      const first = next.images[0];
      router.push(first === undefined ? "/" : `/images/${first.id}`);
    } catch {
      // The save has already committed. A failure to work out what comes next is
      // not a failed save and must not read as one.
      router.push("/");
    }
  };

  const save = () => {
    setSaving(true);
    setSaveFailure(null);

    saveAnnotations(current.id, requestBodyFrom(drafts, centering))
      .then(() => {
        const saved = current.id;
        discardStaged();
        return advance(saved);
      })
      .catch((error: unknown) => {
        setSaveFailure(classifySaveFailure(error));
      })
      .finally(() => {
        setSaving(false);
      });
  };

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

      <SideToggle views={views} showing={current.id} onShow={show} image={image} />

      <Frame
        view={current}
        tool={tool}
        representation={displayed}
        pending={pending}
        drafts={drafts}
        stored={stored}
        onArm={arm}
        onToggleRepresentation={toggleRepresentation}
        onPreview={setPending}
        onPlace={setPending}
        onCycleView={() => {
          const at = views.findIndex((candidate) => candidate.id === current.id);
          const next = views[(at + 1) % views.length];
          if (next) show(next.id);
        }}
      />

      <section className={styles.annotator} aria-label="Annotation controls">
        <div className={styles.tools} role="group" aria-label="What the pointer does">
          <button
            type="button"
            className={tool === "pan" ? styles.toolOn : styles.tool}
            aria-pressed={tool === "pan"}
            onClick={() => {
              arm("pan");
            }}
          >
            Pan
          </button>
          {MARKER_TOOLS.map((entry) => (
            <button
              key={entry.tool}
              type="button"
              className={tool === entry.tool ? styles.toolOn : styles.tool}
              aria-pressed={tool === entry.tool}
              disabled={!armable(entry.tool)}
              title={
                armable(entry.tool)
                  ? undefined
                  : "Only surface defects are marked against the original photograph."
              }
              onClick={() => {
                arm(entry.tool);
              }}
            >
              {entry.label}
            </button>
          ))}
        </div>

        {tool === "centering" ? (
          <CenteringControls
            pending={pending}
            existing={centering}
            cardFrame={current.card_frame ?? WHOLE_ARTIFACT}
            onClearPending={() => {
              setPending(null);
            }}
            onSet={setCentering}
            onClear={() => {
              setCentering(null);
            }}
          />
        ) : null}

        {tool === "corner" || tool === "edge" || tool === "surface" ? (
          <MarkerControls
            tool={tool}
            pending={pending}
            representation={displayed}
            onClearPending={() => {
              setPending(null);
            }}
            onAdd={(marker: MarkerRequest) => {
              setDrafts((current) => [...current, { id: draftId(), marker }]);
            }}
          />
        ) : null}

        {tool === "surface" && displayed === "normalized" ? (
          <p className={styles.warning}>
            One artifact pixel is 83 microns, so a hairline scratch is smaller than a pixel and a
            print line is one or two (ADR 0010). Switch to the original photograph to mark fine
            defects — press <kbd>o</kbd> — and use <em>I cannot tell</em> rather than guessing.
          </p>
        ) : null}

        <h2 className={styles.stagedHeading}>Staged for this view</h2>
        <DraftList
          drafts={drafts}
          centering={centering}
          onRemove={(id) => {
            setDrafts((current) => current.filter((draft) => draft.id !== id));
          }}
        />

        {saveFailure ? (
          <p className={styles.notice} role="alert">
            {FAILURE_MESSAGE[saveFailure]}
          </p>
        ) : null}

        <SaveBar
          drafts={drafts}
          centering={centering}
          hasArtifact={current.has_artifact}
          saving={saving}
          onSave={save}
        />
      </section>

      {stored.length > 0 || image.centering.length > 0 ? (
        <section className={styles.saved} aria-label="Already recorded">
          <h2 className={styles.stagedHeading}>Already recorded</h2>
          {/*
           * Every row, oldest first, and not collapsed to a current reading:
           * both tables are append-only, so a correction is a newer row — and a
           * surface has as many defects as it has, so no single collapsing rule
           * is right for all three kinds.
           */}
          <ul className={styles.drafts}>
            {stored.map((marker) => (
              <li key={marker.id} className={styles.draft}>
                <span>
                  {marker.kind.replace(/_/g, " ")}
                  {marker.region ? ` · ${marker.region.replace(/_/g, " ")}` : ""} ·{" "}
                  {marker.label.replace(/_/g, " ")}
                  {marker.severity ? ` · ${marker.severity}` : ""}
                  {marker.representation === "original" ? " · original photograph" : ""} ·{" "}
                  {marker.annotator_id} · {marker.created_at.slice(0, 10)}
                </span>
              </li>
            ))}
            {image.centering.map((measurement) => (
              <li key={measurement.id} className={styles.draft}>
                <span>
                  centering · {measurement.annotator_id} · {measurement.created_at.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <p className={styles.footnote}>
        <Link
          href="/"
          onClick={(event) => {
            if (!mayLeave()) event.preventDefault();
          }}
        >
          Back to the work list
        </Link>
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

function Frame({
  view,
  tool,
  representation,
  pending,
  drafts,
  stored,
  onArm,
  onToggleRepresentation,
  onPreview,
  onPlace,
  onCycleView,
}: {
  view: AnnotationImageSummary;
  tool: Tool;
  representation: Representation;
  pending: BoundingBox | null;
  drafts: readonly MarkerDraft[];
  stored: readonly StoredMarker[];
  onArm: (tool: Tool) => void;
  onToggleRepresentation: () => void;
  onPreview: (box: BoundingBox | null) => void;
  onPlace: (box: BoundingBox) => void;
  onCycleView: () => void;
}) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState<Size>(FALLBACK_FRAME);
  const [natural, setNatural] = useState<Size | null>(null);
  const [transform, setTransform] = useState<View>({ scale: 1, x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number } | null>(null);

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

      // `o` toggles between the artifact and the original photograph (#175).
      // A no-op where the image has no artifact — there is nothing to toggle to.
      if (event.key === "o" || event.key === "O") {
        event.preventDefault();
        onToggleRepresentation();
        return;
      }

      /*
       * Four letters and an escape, and no more. The label lists come to
       * twenty-eight members between them, and a mnemonic scheme for that many
       * would be a second vocabulary free to drift from the schema's — so
       * choosing a *tool* is a key and choosing a label is a `<select>`. `1` is
       * already actual size, which is why none of these is a digit.
       */
      const armed = TOOL_KEYS.get(event.key.toLowerCase());
      if (armed !== undefined) {
        event.preventDefault();
        onArm(armed);
        onPreview(null);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onArm("pan");
        onPreview(null);
        return;
      }

      const move = moves[event.key];
      if (move === undefined) return;
      event.preventDefault();
      apply(move);
    },
    [apply, frame, onArm, onCycleView, onPreview, onToggleRepresentation],
  );

  return (
    <>
      <p className={representation === "normalized" ? styles.badgeArtifact : styles.badgeOriginal}>
        {representation === "normalized"
          ? "Normalized artifact — coordinates are fractions of this frame."
          : view.has_artifact
            ? "Original photograph — only surface defects are marked against this frame."
            : "Original photograph — no card was located, so only surface marks are taken against this frame."}
        {view.has_artifact ? (
          <button type="button" className={styles.secondary} onClick={onToggleRepresentation}>
            {representation === "normalized"
              ? "View the original photograph"
              : "View the normalized artifact"}
          </button>
        ) : null}
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
          if (event.button !== 0) return;
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
        /*
         * A cancelled pointer — a touch interrupted, a lost capture — used to
         * leave `dragging` set, and the image then panned on every move with no
         * button held. Both handlers, because either can be the one that fires.
         */
        onPointerCancel={() => {
          dragging.current = null;
        }}
        onLostPointerCapture={() => {
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
            representation === "normalized" ? "normalized artifact" : "original photograph"
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

        {natural === null ? null : (
          <Overlay
            view={transform}
            image={natural}
            drafts={drafts}
            stored={stored}
            pending={pending}
            shown={representation}
          />
        )}

        {/*
         * Mounted only while a tool is armed, which is what makes the mode a
         * question of *which element exists* rather than a branch inside the pan
         * handler above. Disarming unmounts it and panning works again with
         * nothing to reset.
         */}
        {tool !== "pan" && natural !== null ? (
          <CaptureLayer view={transform} image={natural} onPreview={onPreview} onPlace={onPlace} />
        ) : null}
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
        <kbd>f</kbd> other side · <kbd>o</kbd> original / artifact · <kbd>c</kbd> corner ·{" "}
        <kbd>e</kbd> edge · <kbd>s</kbd> surface · <kbd>m</kbd> centering · <kbd>Esc</kbd> back to
        panning
      </p>
    </>
  );
}
