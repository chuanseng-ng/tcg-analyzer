"use client";

import { useMemo, useRef, useState } from "react";

import {
  boxFrom,
  CANNOT_TELL_CONFIDENCE,
  type CardFrame,
  centeringFrom,
  CONFIDENCE_LEVELS,
  CORNER_LABELS,
  CORNER_REGIONS,
  EDGE_LABELS,
  EDGE_REGIONS,
  markerRepresentation,
  readable,
  requiresArtifact,
  requiresSeverity,
  SEVERITIES,
  SURFACE_LABELS,
  type BoundingBox,
  type CenteringRequest,
  type CornerLabel,
  type CornerRegion,
  type DefectSeverity,
  type EdgeLabel,
  type EdgeRegion,
  type MarkerDraft,
  type MarkerRequest,
  type SurfaceLabel,
} from "@/lib/annotations";
import type { Representation, StoredMarker } from "@/lib/api";
import { fractionAt, transformOf, type Size, type View } from "@/lib/viewport";

import styles from "./page.module.css";

/** What the pointer does inside the frame. `pan` is what #159 shipped. */
export type Tool = "pan" | "corner" | "edge" | "surface" | "centering";

export const MARKER_TOOLS = [
  { tool: "corner", key: "c", label: "Corner" },
  { tool: "edge", key: "e", label: "Edge" },
  { tool: "surface", key: "s", label: "Surface" },
  { tool: "centering", key: "m", label: "Centering" },
] as const satisfies readonly { tool: Tool; key: string; label: string }[];

/** Which key arms which tool, for the frame's own handler. */
export const TOOL_KEYS: ReadonlyMap<string, Tool> = new Map(
  MARKER_TOOLS.map((entry) => [entry.key, entry.tool]),
);

// ---------------------------------------------------------------------------
// The overlay
// ---------------------------------------------------------------------------

/**
 * Where a box drawn on the artifact appears on screen.
 *
 * An `<svg>` whose `viewBox` is the unit square, sized to the artifact and given
 * the *same* transform the `<img>` gets. That is what makes a stored fraction an
 * SVG coordinate directly: nothing here multiplies by a width, so there is no
 * second copy of `lib/viewport.ts`'s map to drift from the first.
 *
 * `pointer-events: none` — every gesture belongs to the frame or to the capture
 * layer below, never to a shape. And `vector-effect="non-scaling-stroke"`, or the
 * outline would be eight pixels thick at `MAX_SCALE` and hide the corner it is
 * drawn around.
 */
export function Overlay({
  view,
  image,
  drafts,
  stored,
  pending,
  shown,
}: {
  view: View;
  image: Size;
  drafts: readonly MarkerDraft[];
  stored: readonly StoredMarker[];
  pending: BoundingBox | null;
  shown: Representation;
}) {
  /*
   * Only the rects whose frame is on screen. The two frames relate by a
   * projective warp, so a fraction of one means nothing on the other — a box is
   * filtered out rather than ever projected across (#175). The filter reads the
   * *displayed-representation state*, not what has finished loading, so a
   * wrong-frame rect never renders during the swap.
   */
  const storedShown = stored.filter((marker) => marker.representation === shown);
  const draftsShown = drafts.filter((draft) => markerRepresentation(draft.marker) === shown);
  return (
    <svg
      className={styles.overlay}
      width={image.width}
      height={image.height}
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      style={{ transform: transformOf(view) }}
      aria-hidden="true"
    >
      {storedShown.map((marker) =>
        marker.bbox ? (
          <rect
            key={marker.id}
            className={styles.markStored}
            x={marker.bbox.x}
            y={marker.bbox.y}
            width={marker.bbox.width}
            height={marker.bbox.height}
            vectorEffect="non-scaling-stroke"
          >
            <title>{`${readable(marker.kind)} — ${readable(marker.label)} (saved)`}</title>
          </rect>
        ) : null,
      )}
      {draftsShown.map((draft) =>
        draft.marker.bbox ? (
          <rect
            key={draft.id}
            className={styles.markDraft}
            x={draft.marker.bbox.x}
            y={draft.marker.bbox.y}
            width={draft.marker.bbox.width}
            height={draft.marker.bbox.height}
            vectorEffect="non-scaling-stroke"
          >
            <title>{`${readable(draft.marker.kind)} — ${readable(draft.marker.label)}`}</title>
          </rect>
        ) : null,
      )}
      {pending ? (
        <rect
          className={styles.markPending}
          x={pending.x}
          y={pending.y}
          width={pending.width}
          height={pending.height}
          vectorEffect="non-scaling-stroke"
        >
          <title>Where you are placing it</title>
        </rect>
      ) : null}
    </svg>
  );
}

/**
 * The layer that takes a drag while a tool is armed.
 *
 * It exists only while one is, which is what keeps pan's handlers untouched: the
 * mode is *which element is mounted*, not a branch inside the one handler #159
 * wrote. Disarming unmounts this and panning works again with nothing to reset.
 */
export function CaptureLayer({
  view,
  image,
  onPreview,
  onPlace,
}: {
  view: View;
  image: Size;
  onPreview: (box: BoundingBox | null) => void;
  onPlace: (box: BoundingBox) => void;
}) {
  // A ref rather than state, and deliberately: releasing the mouse fires
  // `pointerup` and then — because the browser drops pointer capture — a
  // `lostpointercapture` straight after it. The abandon handler must see that
  // the drag already ended, or it wipes the box `pointerup` just placed; a
  // state update would not be visible until the next render, a ref is.
  const from = useRef<{ x: number; y: number } | null>(null);

  const pointOn = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return fractionAt(view, image, {
      width: event.clientX - box.left,
      height: event.clientY - box.top,
    });
  };

  // The abandon path — a cancelled pointer, or capture lost mid-drag. Stands
  // down when no drag is in progress, which is exactly the post-`pointerup`
  // `lostpointercapture` case.
  const abandon = () => {
    if (from.current === null) return;
    from.current = null;
    onPreview(null);
  };

  return (
    <div
      className={styles.capture}
      onPointerDown={(event) => {
        // Primary button only. A right-click here would otherwise start a drag
        // the context menu then strands, which is the bug the frame had.
        if (event.button !== 0) return;
        // Stopped here, or the frame underneath starts a pan from the same
        // press and — because its handler also calls `setPointerCapture`, and
        // the last call wins — steals the whole pointer stream: the view pans
        // under the box and this layer never sees the pointerup that would
        // have placed it. Covering the frame does not stop bubbling; this
        // does, and it is what keeps the frame's pan handlers untouched.
        event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        from.current = pointOn(event);
      }}
      onPointerMove={(event) => {
        if (from.current === null) return;
        event.stopPropagation();
        onPreview(boxFrom(from.current, pointOn(event)));
      }}
      onPointerUp={(event) => {
        if (from.current === null) return;
        event.stopPropagation();
        const box = boxFrom(from.current, pointOn(event));
        from.current = null;
        // A click that did not move is not a region — `bbox_width > 0` is a
        // CHECK, and a marker made from one would be refused after the annotator
        // thought they had placed it. Its preview is cleared; a placed box
        // replaces the preview by being set over it.
        if (box !== null) onPlace(box);
        else onPreview(null);
      }}
      onPointerCancel={abandon}
      onLostPointerCapture={abandon}
    />
  );
}

// ---------------------------------------------------------------------------
// The forms
// ---------------------------------------------------------------------------

interface MarkerFormState {
  readonly cornerRegion: CornerRegion;
  readonly edgeRegion: EdgeRegion;
  readonly label: string;
  readonly severity: DefectSeverity | null;
  readonly confidence: number | null;
}

const EMPTY_FORM: MarkerFormState = {
  cornerRegion: "top_left",
  edgeRegion: "top",
  label: "",
  severity: null,
  // Deliberately null. `image_annotations.confidence` is NOT NULL with no server
  // default precisely so that nobody's silence reads as certainty, and a
  // pre-selected radio would put that default back where the schema cannot see it.
  confidence: null,
};

function labelsFor(tool: Tool): readonly string[] {
  if (tool === "corner") return CORNER_LABELS;
  if (tool === "edge") return EDGE_LABELS;
  return SURFACE_LABELS;
}

function markerFrom(
  tool: Tool,
  form: MarkerFormState,
  bbox: BoundingBox | null,
  representation: Representation,
): MarkerRequest {
  const shared = {
    severity: form.severity,
    confidence: form.confidence ?? 0,
    bbox,
  };

  if (tool === "corner") {
    return {
      kind: "corner",
      region: form.cornerRegion,
      label: form.label as CornerLabel,
      ...shared,
    };
  }
  if (tool === "edge") {
    return { kind: "edge", region: form.edgeRegion, label: form.label as EdgeLabel, ...shared };
  }
  // Only the surface names a frame: the marker is stamped with whichever
  // representation was on screen when it was added (#175), and the service
  // writes 'normalized' for the other two kinds itself.
  return { kind: "surface", label: form.label as SurfaceLabel, representation, ...shared };
}

/**
 * The controls that turn a drag into a marker.
 *
 * Label, severity and confidence are a native form rather than more shortcuts:
 * §14, §15 and §16 come to twenty-eight labels between them, and a mnemonic
 * scheme for that many would be a second vocabulary free to drift. The frame
 * keeps the four keys that matter — the ones that arm a tool — and everything
 * here is reachable with Tab because it is a `<fieldset>` of real inputs.
 */
export function MarkerControls({
  tool,
  pending,
  representation,
  onClearPending,
  onAdd,
}: {
  tool: Exclude<Tool, "pan" | "centering">;
  pending: BoundingBox | null;
  representation: Representation;
  onClearPending: () => void;
  onAdd: (marker: MarkerRequest) => void;
}) {
  const [form, setForm] = useState<MarkerFormState>(EMPTY_FORM);
  const labels = labelsFor(tool);
  const needsSeverity = form.label !== "" && requiresSeverity(form.label);
  const complete =
    form.label !== "" && form.confidence !== null && (!needsSeverity || form.severity !== null);

  const reset = () => {
    setForm((current) => ({
      ...EMPTY_FORM,
      cornerRegion: current.cornerRegion,
      edgeRegion: current.edgeRegion,
    }));
    onClearPending();
  };

  return (
    <form
      className={styles.controlPanel}
      onSubmit={(event) => {
        event.preventDefault();
        if (!complete) return;
        onAdd(markerFrom(tool, form, pending, representation));
        reset();
      }}
    >
      {tool === "corner" ? (
        <fieldset className={styles.fieldset}>
          <legend>Which corner</legend>
          {CORNER_REGIONS.map((region) => (
            <label key={region} className={styles.choice}>
              <input
                type="radio"
                name="corner-region"
                checked={form.cornerRegion === region}
                onChange={() => {
                  setForm((current) => ({ ...current, cornerRegion: region }));
                }}
              />
              {readable(region)}
            </label>
          ))}
        </fieldset>
      ) : null}

      {tool === "edge" ? (
        <fieldset className={styles.fieldset}>
          <legend>Which edge</legend>
          {EDGE_REGIONS.map((region) => (
            <label key={region} className={styles.choice}>
              <input
                type="radio"
                name="edge-region"
                checked={form.edgeRegion === region}
                onChange={() => {
                  setForm((current) => ({ ...current, edgeRegion: region }));
                }}
              />
              {readable(region)}
            </label>
          ))}
        </fieldset>
      ) : null}

      <p className={styles.field}>
        <label htmlFor="marker-label">What is there</label>
        <select
          id="marker-label"
          value={form.label}
          onChange={(event) => {
            const label = event.target.value;
            setForm((current) => ({
              ...current,
              label,
              // A label that asserts no defect has nothing to rate, and the
              // schema refuses a severity beside one. Clearing it here means the
              // annotator never has to work that out from a 422.
              severity: requiresSeverity(label) ? current.severity : null,
            }));
          }}
        >
          <option value="">Choose…</option>
          {labels.map((label) => (
            <option key={label} value={label}>
              {readable(label)}
            </option>
          ))}
        </select>
      </p>

      <fieldset className={styles.fieldset} disabled={!needsSeverity}>
        <legend>How bad</legend>
        {SEVERITIES.map((severity) => (
          <label key={severity} className={styles.choice}>
            <input
              type="radio"
              name="severity"
              checked={form.severity === severity}
              onChange={() => {
                setForm((current) => ({ ...current, severity }));
              }}
            />
            {severity}
          </label>
        ))}
        {!needsSeverity && form.label !== "" ? (
          <span className={styles.hint}>Nothing to rate.</span>
        ) : null}
      </fieldset>

      <ConfidenceChoice
        value={form.confidence}
        onChange={(confidence) => {
          setForm((current) => ({ ...current, confidence }));
        }}
        name="marker-confidence"
      />

      {/*
       * Uncertainty in one action, which is the point rather than a convenience:
       * if admitting you cannot tell costs three clicks and guessing costs one,
       * the corpus fills with confident guesses — and a model trained on those is
       * the confidently-wrong output this product's invariants forbid. `unknown`
       * carries no severity, so nothing else is needed to make it savable.
       */}
      <p>
        <button
          type="button"
          className={styles.secondary}
          onClick={() => {
            setForm((current) => ({
              ...current,
              label: "unknown",
              severity: null,
              confidence: CANNOT_TELL_CONFIDENCE,
            }));
          }}
        >
          I cannot tell
        </button>
      </p>

      <p className={styles.placement}>
        {pending
          ? "Placed. Drag again to move it."
          : "Drag on the image to place it, or add it without a box — a corner's region already says where it is."}
      </p>

      <p className={styles.actions}>
        <button type="submit" disabled={!complete}>
          Add {tool}
        </button>
        {pending ? (
          <button type="button" className={styles.secondary} onClick={onClearPending}>
            Clear the box
          </button>
        ) : null}
      </p>
    </form>
  );
}

function ConfidenceChoice({
  value,
  onChange,
  name,
}: {
  value: number | null;
  onChange: (value: number) => void;
  name: string;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend>How sure are you</legend>
      {CONFIDENCE_LEVELS.map((level) => (
        <label key={level.value} className={styles.choice}>
          <input
            type="radio"
            name={name}
            checked={value === level.value}
            onChange={() => {
              onChange(level.value);
            }}
          />
          {level.label}
        </label>
      ))}
    </fieldset>
  );
}

/**
 * Centering, measured rather than typed.
 *
 * The annotator drags a box round the inner frame and the two ratios follow from
 * where its edges sit — spec §21 asks for a measurement, and somebody doing
 * division under time pressure is not one. The borders are what lies between the
 * box and the card's own edge — the service reports where that edge sits inside
 * the artifact, because #194 put a margin of photograph around it.
 *
 * Each axis can be switched off, and that is not a convenience either: §21 names
 * full-art and borderless layouts outright, and a card with no border on an axis
 * has no ratio there. `null` says so; `0.5` would be a fabrication.
 */
export function CenteringControls({
  pending,
  existing,
  cardFrame,
  onClearPending,
  onSet,
  onClear,
}: {
  pending: BoundingBox | null;
  existing: CenteringRequest | null;
  cardFrame: CardFrame;
  onClearPending: () => void;
  onSet: (reading: CenteringRequest) => void;
  onClear: () => void;
}) {
  const [measuresHorizontal, setMeasuresHorizontal] = useState(true);
  const [measuresVertical, setMeasuresVertical] = useState(true);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [notes, setNotes] = useState("");

  const ratios = useMemo(
    () =>
      pending === null
        ? null
        : centeringFrom(
            pending,
            { horizontal: measuresHorizontal, vertical: measuresVertical },
            cardFrame,
          ),
    [pending, measuresHorizontal, measuresVertical, cardFrame],
  );

  const complete = ratios !== null && confidence !== null;

  return (
    <form
      className={styles.controlPanel}
      onSubmit={(event) => {
        event.preventDefault();
        if (ratios === null || confidence === null) return;
        onSet({ ...ratios, confidence, notes: notes.trim() === "" ? null : notes.trim() });
        onClearPending();
        setNotes("");
        setConfidence(null);
      }}
    >
      <p className={styles.placement}>
        Drag a box round the inner frame — the printed border of the artwork. The borders are what
        lies between it and the card&apos;s edge; the margin beyond the card does not count.
      </p>

      <fieldset className={styles.fieldset}>
        <legend>Which axes have a border</legend>
        <label className={styles.choice}>
          <input
            type="checkbox"
            checked={measuresHorizontal}
            onChange={(event) => {
              setMeasuresHorizontal(event.target.checked);
            }}
          />
          Left and right
        </label>
        <label className={styles.choice}>
          <input
            type="checkbox"
            checked={measuresVertical}
            onChange={(event) => {
              setMeasuresVertical(event.target.checked);
            }}
          />
          Top and bottom
        </label>
      </fieldset>

      <output className={styles.derived}>
        {ratios === null
          ? pending === null
            ? "No box yet."
            : "That box leaves no border to measure against."
          : `${describeRatio("Horizontal", ratios.horizontal)} · ${describeRatio("Vertical", ratios.vertical)}`}
      </output>

      <ConfidenceChoice value={confidence} onChange={setConfidence} name="centering-confidence" />

      <p className={styles.field}>
        <label htmlFor="centering-notes">Anything odd about the layout</label>
        <textarea
          id="centering-notes"
          value={notes}
          rows={2}
          maxLength={2000}
          onChange={(event) => {
            setNotes(event.target.value);
          }}
        />
      </p>

      <p className={styles.actions}>
        <button type="submit" disabled={!complete}>
          Set the centering
        </button>
        {existing ? (
          <button type="button" className={styles.secondary} onClick={onClear}>
            Remove the measurement
          </button>
        ) : null}
      </p>
    </form>
  );
}

/** `0.5` is perfect, so say how far off it is rather than making somebody subtract. */
function describeRatio(axis: string, value: number | null): string {
  if (value === null) return `${axis}: no border`;
  return `${axis}: ${(value * 100).toFixed(1)} / ${((1 - value) * 100).toFixed(1)}`;
}

// ---------------------------------------------------------------------------
// What is staged, and saving it
// ---------------------------------------------------------------------------

export function DraftList({
  drafts,
  centering,
  onRemove,
}: {
  drafts: readonly MarkerDraft[];
  centering: CenteringRequest | null;
  onRemove: (id: string) => void;
}) {
  if (drafts.length === 0 && centering === null) {
    return <p className={styles.hint}>Nothing staged for this view yet.</p>;
  }

  return (
    <ul className={styles.drafts}>
      {drafts.map((draft) => (
        <li key={draft.id} className={styles.draft}>
          <span>
            {readable(draft.marker.kind)}
            {"region" in draft.marker ? ` · ${readable(draft.marker.region)}` : ""} ·{" "}
            {readable(draft.marker.label)}
            {draft.marker.severity ? ` · ${draft.marker.severity}` : ""}
            {draft.marker.bbox ? "" : " · no box"}
          </span>
          <button
            type="button"
            className={styles.secondary}
            onClick={() => {
              onRemove(draft.id);
            }}
          >
            Remove
          </button>
        </li>
      ))}
      {centering ? (
        <li className={styles.draft}>
          <span>
            centering · {describeRatio("H", centering.horizontal ?? null)} ·{" "}
            {describeRatio("V", centering.vertical ?? null)}
          </span>
        </li>
      ) : null}
    </ul>
  );
}

export function SaveBar({
  drafts,
  centering,
  hasArtifact,
  saving,
  onSave,
}: {
  drafts: readonly MarkerDraft[];
  centering: CenteringRequest | null;
  hasArtifact: boolean;
  saving: boolean;
  onSave: () => void;
}) {
  const count = drafts.length + (centering === null ? 0 : 1);
  // The service refuses claims about an artifact that does not exist. Asking
  // the same question here means the annotator finds out while they can still
  // change it, rather than from a 409 after twenty minutes. Surface work
  // declared against the original photograph passes — the photograph exists.
  const refusable = !hasArtifact && requiresArtifact(drafts, centering);

  return (
    <div className={styles.saveBar}>
      <button
        type="button"
        className={styles.save}
        disabled={count === 0 || saving}
        onClick={onSave}
      >
        {saving ? "Saving…" : `Save ${String(count)} and take the next image`}
      </button>
      {refusable ? (
        <p className={styles.warning} role="alert">
          This image has no artifact, so nothing staged against one can be stored. Remove the corner
          and edge boxes and the centering measurement — surface marks against the original
          photograph are fine — or normalize the image first.
        </p>
      ) : null}
      {count > 0 ? <p className={styles.hint}>Nothing is written until you save.</p> : null}
    </div>
  );
}
