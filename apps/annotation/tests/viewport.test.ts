import { describe, expect, it } from "vitest";

import {
  actualSize,
  clamp,
  fitScale,
  fitted,
  fractionAt,
  MAX_SCALE,
  pan,
  showsRealPixels,
  transformOf,
  zoom,
  zoomAt,
  ZOOM_STEP,
  type Size,
  type View,
} from "@/lib/viewport";

/** A frame narrower than the artifact is tall — the ordinary case on a laptop. */
const FRAME: Size = { width: 600, height: 800 };
const ARTIFACT: Size = { width: 756, height: 1056 };

/**
 * The invariant, as a predicate.
 *
 * On each axis: if the scaled image is no bigger than the frame it is centred,
 * and otherwise the frame is fully covered — the leading edge never positive,
 * the trailing edge never inside. There is no third case, and "a blank margin
 * is visible" is precisely the negation.
 */
function coversTheFrame(view: View, frame: Size, image: Size): boolean {
  const check = (offset: number, frameLength: number, contentLength: number) =>
    contentLength <= frameLength
      ? Math.abs(offset - (frameLength - contentLength) / 2) < 1e-9
      : offset <= 1e-9 && offset >= frameLength - contentLength - 1e-9;

  return (
    check(view.x, frame.width, image.width * view.scale) &&
    check(view.y, frame.height, image.height * view.scale)
  );
}

/** Deterministic, so a failure is reproducible without a seed to hunt for. */
function pseudoRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 1_103_515_245 + 12_345) % 2_147_483_648;
    return state / 2_147_483_648;
  };
}

describe("fitScale", () => {
  it("chooses the axis that runs out first", () => {
    // 800/1056 is smaller than 600/756, so height is the binding constraint.
    expect(fitScale(FRAME, ARTIFACT)).toBeCloseTo(800 / 1056, 12);
  });

  it("answers rather than dividing by zero before the image has loaded", () => {
    expect(fitScale(FRAME, { width: 0, height: 0 })).toBe(1);
    expect(fitScale({ width: 0, height: 0 }, ARTIFACT)).toBe(1);
  });
});

describe("the starting view", () => {
  it("shows the whole image, centred", () => {
    const view = fitted(FRAME, ARTIFACT);

    expect(view.scale).toBeCloseTo(fitScale(FRAME, ARTIFACT), 12);
    expect(ARTIFACT.height * view.scale).toBeLessThanOrEqual(FRAME.height + 1e-9);
    expect(coversTheFrame(view, FRAME, ARTIFACT)).toBe(true);
  });
});

describe("the clamp", () => {
  it("never lets the image leave the frame, at any scale, after any pan", () => {
    // The issue's required boundary test. A sweep rather than three cases,
    // because the failure this guards against is an *edge* — one combination of
    // scale and offset where a margin opens up — and picking the combinations by
    // hand is picking the ones already believed to work.
    const random = pseudoRandom(20_260_829);

    for (let attempt = 0; attempt < 500; attempt += 1) {
      const view = clamp(
        {
          scale: random() * (MAX_SCALE + 2) - 1,
          x: (random() - 0.5) * 6000,
          y: (random() - 0.5) * 6000,
        },
        FRAME,
        ARTIFACT,
      );

      expect(coversTheFrame(view, FRAME, ARTIFACT)).toBe(true);
    }
  });

  it("holds after an arbitrary sequence of pans and zooms", () => {
    const random = pseudoRandom(4_242);
    let view = fitted(FRAME, ARTIFACT);

    for (let step = 0; step < 500; step += 1) {
      view =
        random() < 0.5
          ? pan(view, (random() - 0.5) * 2000, (random() - 0.5) * 2000, FRAME, ARTIFACT)
          : zoom(view, random() < 0.5 ? ZOOM_STEP : 1 / ZOOM_STEP, FRAME, ARTIFACT);

      expect(coversTheFrame(view, FRAME, ARTIFACT)).toBe(true);
    }
  });

  it("centres an image smaller than the frame rather than letting it drift", () => {
    const small: Size = { width: 100, height: 100 };
    const view = pan(fitted(FRAME, small), 400, 400, FRAME, small);

    expect(view.x).toBeCloseTo((FRAME.width - small.width * view.scale) / 2, 9);
    expect(view.y).toBeCloseTo((FRAME.height - small.height * view.scale) / 2, 9);
  });
});

describe("the scale limits", () => {
  it("stops at fit however far out you zoom", () => {
    let view = fitted(FRAME, ARTIFACT);
    for (let step = 0; step < 40; step += 1) {
      view = zoom(view, 1 / ZOOM_STEP, FRAME, ARTIFACT);
    }

    expect(view.scale).toBeCloseTo(fitScale(FRAME, ARTIFACT), 12);
  });

  it("stops at the maximum however far in you zoom", () => {
    let view = fitted(FRAME, ARTIFACT);
    for (let step = 0; step < 60; step += 1) {
      view = zoom(view, ZOOM_STEP, FRAME, ARTIFACT);
    }

    expect(view.scale).toBe(MAX_SCALE);
  });

  it("magnifies a corner far enough to judge it", () => {
    // A corner is roughly 3% of a 756px-wide artifact — about 22px. At the
    // maximum that is ~180 CSS pixels, which is what "zoom to judge a corner"
    // has to mean at this artifact size. If this number ever has to fall, that
    // is a finding about ml/normalization's output size, not about this file.
    const corner = ARTIFACT.width * 0.03;

    expect(corner * MAX_SCALE).toBeGreaterThan(150);
  });
});

describe("zooming about a point", () => {
  it("keeps what is under the pointer under the pointer", () => {
    const start = fitted(FRAME, ARTIFACT);
    const focus: Size = { width: 420, height: 260 };
    // Which image pixel sits under the focus before the zoom.
    const before = {
      x: (focus.width - start.x) / start.scale,
      y: (focus.height - start.y) / start.scale,
    };

    const after = zoomAt(start, ZOOM_STEP, focus, FRAME, ARTIFACT);
    const now = {
      x: (focus.width - after.x) / after.scale,
      y: (focus.height - after.y) / after.scale,
    };

    // Only the axis that is not clamped can hold exactly: at fit, the image
    // fills the height and is centred horizontally, so x is pinned by the
    // clamp and y is free.
    expect(now.y).toBeCloseTo(before.y, 6);
  });
});

describe("actual size", () => {
  it("is one artifact pixel per CSS pixel", () => {
    expect(actualSize(fitted(FRAME, ARTIFACT), FRAME, ARTIFACT).scale).toBe(1);
  });
});

describe("the rendering hint", () => {
  it("shows real pixels only past 1:1, where there is nothing left to interpolate", () => {
    expect(showsRealPixels({ scale: 0.75, x: 0, y: 0 })).toBe(false);
    expect(showsRealPixels({ scale: 1, x: 0, y: 0 })).toBe(false);
    expect(showsRealPixels({ scale: 1.5, x: 0, y: 0 })).toBe(true);
  });
});

describe("the transform", () => {
  it("translates before it scales, which is what the arithmetic assumes", () => {
    expect(transformOf({ scale: 2, x: -30, y: -40 })).toBe("translate(-30px, -40px) scale(2)");
  });
});

/*
 * The inverse map — #160.
 *
 * This is the test the issue asks for by name: the one that fails loudly if the
 * viewer's transform changes. Every stored coordinate is produced by
 * `fractionAt`, so a change to `transformOf` that this did not catch would move
 * every annotation in the corpus without touching a row.
 */
describe("screen to artifact", () => {
  const at = (view: View, x: number, y: number) =>
    fractionAt(view, ARTIFACT, { width: x, height: y });

  it("inverts the forward map exactly, at every magnification", () => {
    for (const scale of [fitScale(FRAME, ARTIFACT), 1, 2, MAX_SCALE]) {
      const view: View = { scale, x: -37, y: -91 };

      for (const point of [
        { x: 0.25, y: 0.75 },
        { x: 0.5, y: 0.5 },
        { x: 0.9, y: 0.1 },
      ]) {
        // Forward: fraction -> artifact pixels -> frame pixels.
        const frameX = point.x * ARTIFACT.width * view.scale + view.x;
        const frameY = point.y * ARTIFACT.height * view.scale + view.y;

        const back = at(view, frameX, frameY);

        expect(back.x).toBeCloseTo(point.x, 10);
        expect(back.y).toBeCloseTo(point.y, 10);
      }
    }
  });

  it("puts the artifact's own corners at 0 and 1", () => {
    const view = fitted(FRAME, ARTIFACT);

    expect(at(view, view.x, view.y)).toEqual({ x: 0, y: 0 });
    expect(
      at(view, view.x + ARTIFACT.width * view.scale, view.y + ARTIFACT.height * view.scale),
    ).toEqual({ x: 1, y: 1 });
  });

  it("clamps a point outside the artifact into the unit square", () => {
    const view = fitted(FRAME, ARTIFACT);

    // Dragging off the top-left of the image, and off the bottom-right.
    expect(at(view, view.x - 500, view.y - 500)).toEqual({ x: 0, y: 0 });
    expect(at(view, view.x + 99_999, view.y + 99_999)).toEqual({ x: 1, y: 1 });
  });

  it("answers with the origin rather than a NaN when there is nothing to divide by", () => {
    // `scale` is never zero through `clamp`, but a fraction is what a coordinate
    // is *stored* as — `NaN` would reach the wire as `null`, which the schema
    // reads as a deliberate absence rather than as an accident.
    expect(at({ scale: 0, x: 0, y: 0 }, 10, 10)).toEqual({ x: 0, y: 0 });
  });
});
