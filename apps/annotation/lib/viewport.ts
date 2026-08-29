/**
 * Pan and zoom, as arithmetic.
 *
 * Kept out of the component on purpose. The one property that matters here —
 * *the image never leaves the frame, at any scale, after any pan* — is a claim
 * about numbers, and asserting it against a rendered DOM would test jsdom's
 * layout rather than the rule. `tests/viewport.test.ts` sweeps it directly.
 *
 * The transform is `translate(x, y) scale(s)` with `transform-origin: 0 0`,
 * which makes it a plain affine map from image pixels to frame pixels:
 *
 *     frame = image * scale + offset
 *
 * An origin of `50% 50%` would put the frame's size inside every equation below
 * for no benefit, so it is fixed in CSS and never varied.
 *
 * No library. This is roughly forty lines of arithmetic, and a pan-zoom
 * dependency would be forty lines of arithmetic plus a dependency.
 */

/** How far in the annotator can go. */
export const MAX_SCALE = 8;

/**
 * One press of the zoom key, and one wheel notch.
 *
 * 1.25 rather than 2: spec §21's corners and edges are judged by comparing
 * neighbouring magnifications, and a factor that doubles skips the one the
 * annotator wanted.
 */
export const ZOOM_STEP = 1.25;

/** One press of an arrow key, as a fraction of the frame. `Shift` multiplies it. */
export const PAN_STEP = 0.1;
export const PAN_STEP_LARGE = 0.5;

export interface Size {
  readonly width: number;
  readonly height: number;
}

export interface View {
  /** Image pixels to frame pixels. `1` is one artifact pixel per CSS pixel. */
  readonly scale: number;
  readonly x: number;
  readonly y: number;
}

/**
 * The scale at which the whole image is visible.
 *
 * This is the *minimum*, not merely the initial value: zooming out past it would
 * shrink the image inside a frame it already fits, which is motion with nothing
 * to look at.
 */
export function fitScale(frame: Size, image: Size): number {
  if (image.width <= 0 || image.height <= 0 || frame.width <= 0 || frame.height <= 0) {
    return 1;
  }

  return Math.min(frame.width / image.width, frame.height / image.height);
}

function clampAxis(offset: number, frameLength: number, contentLength: number): number {
  if (contentLength <= frameLength) {
    // Smaller than the frame: centred, and the offset is not the annotator's to
    // choose. Allowing a pan here would let a fitted image drift off-centre and
    // then off-screen.
    return (frameLength - contentLength) / 2;
  }

  // Larger than the frame: the leading edge may not go positive and the
  // trailing edge may not come inside, so the frame stays fully covered.
  return Math.min(0, Math.max(frameLength - contentLength, offset));
}

/**
 * The single rule that keeps the image on screen.
 *
 * **Every function below returns through this**, which is what makes "no code
 * path can lose the image" true by construction rather than by review. A new
 * gesture added later is safe if and only if it does the same.
 */
export function clamp(view: View, frame: Size, image: Size): View {
  const scale = Math.min(MAX_SCALE, Math.max(fitScale(frame, image), view.scale));

  return {
    scale,
    x: clampAxis(view.x, frame.width, image.width * scale),
    y: clampAxis(view.y, frame.height, image.height * scale),
  };
}

/** The starting view: the whole image, centred. */
export function fitted(frame: Size, image: Size): View {
  return clamp({ scale: fitScale(frame, image), x: 0, y: 0 }, frame, image);
}

/**
 * Zoom about a point in the frame, keeping whatever is under it under it.
 *
 * That is what makes wheel zoom feel like a magnifier rather than a slider: the
 * corner an annotator is pointing at is the corner they get. `focus` is in frame
 * coordinates — a pointer position, or the frame's centre for the keyboard.
 */
export function zoomAt(view: View, factor: number, focus: Size, frame: Size, image: Size): View {
  const scale = Math.min(MAX_SCALE, Math.max(fitScale(frame, image), view.scale * factor));
  const ratio = scale / view.scale;

  return clamp(
    {
      scale,
      x: focus.width - (focus.width - view.x) * ratio,
      y: focus.height - (focus.height - view.y) * ratio,
    },
    frame,
    image,
  );
}

/** Zoom about the frame's centre — what the keyboard and the buttons do. */
export function zoom(view: View, factor: number, frame: Size, image: Size): View {
  return zoomAt(view, factor, { width: frame.width / 2, height: frame.height / 2 }, frame, image);
}

/** Move the image by a frame-pixel delta. */
export function pan(view: View, dx: number, dy: number, frame: Size, image: Size): View {
  return clamp({ scale: view.scale, x: view.x + dx, y: view.y + dy }, frame, image);
}

/** One artifact pixel per CSS pixel, about the frame's centre. */
export function actualSize(view: View, frame: Size, image: Size): View {
  return zoom(view, 1 / view.scale, frame, image);
}

/** The CSS the frame applies. Origin `0 0` is what the arithmetic above assumes. */
export function transformOf(view: View): string {
  return `translate(${String(view.x)}px, ${String(view.y)}px) scale(${String(view.scale)})`;
}

/**
 * Whether to stop the browser smoothing the image.
 *
 * Above 1:1 there is nothing left to interpolate *from*: an annotator judging a
 * soft corner needs the artifact's real pixels, and a smoothed guess about a
 * corner is exactly the kind of confident fiction this product's invariants
 * refuse elsewhere.
 */
export function showsRealPixels(view: View): boolean {
  return view.scale > 1;
}

/** A point on the artifact, as fractions of its width and height. */
export interface Fraction {
  readonly x: number;
  readonly y: number;
}

/**
 * Where a point in the frame falls on the artifact.
 *
 * The inverse of the map at the top of this file: `image = (frame - offset) / scale`,
 * then divided by the artifact's own size. It lives here rather than beside the
 * annotation code because the forward map is here — a transform in one file and
 * its inverse in another is how the two come to disagree.
 *
 * **This is the clamp that keeps a stored coordinate inside the unit square**,
 * and it is not `clamp` above: that one keeps the image inside the frame and is
 * about the `View`, this one keeps a fraction inside the artifact and is about a
 * point. Clamping each corner and *then* taking the extent is what makes
 * `bbox_x + bbox_width <= 1` true by construction, so the schema's constraint
 * cannot fire on a drag that left the frame.
 */
export function fractionAt(view: View, image: Size, point: Size): Fraction {
  return {
    x: unitInterval((point.width - view.x) / view.scale / image.width),
    y: unitInterval((point.height - view.y) / view.scale / image.height),
  };
}

function unitInterval(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}
