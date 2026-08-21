import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Mobile-first is a requirement, not a preference: the primary input device is
 * a phone camera. jsdom has no layout engine, so it cannot be asked whether a
 * 375px viewport scrolls horizontally. These tests assert the property that
 * causes that overflow instead — a fixed pixel width in a layout primitive.
 *
 * The real 375px browser assertion belongs to the E2E milestone; see the
 * `docs/` note on deferred viewport checks.
 *
 * `max-width` and `max-inline-size` are deliberately *not* caught: a maximum
 * still lets the box shrink to the device, which is the whole point.
 */

const MODULES = [
  "components/Container.module.css",
  "components/Stack.module.css",
  // The catalog browse surface (#30) — a results list is the widest thing the
  // product renders before the results UI arrives, so it is held to the same
  // rule as the primitives it is built from.
  "app/cards/page.module.css",
  "app/cards/CardSearch.module.css",
  "app/cards/CardResults.module.css",
  "app/cards/[cardId]/page.module.css",
  // The upload screen (#34) — the first surface a phone camera feeds, and the
  // one screen this product is genuinely mobile-first *for*.
  "app/analyze/page.module.css",
  "app/analyze/CardUpload.module.css",
  // The identification-confirmation gate (#91). It carries the M1 acceptance
  // criterion, so it is the last screen that may be unusable on a phone.
  "app/identify/page.module.css",
] as const;

// Vitest runs with `apps/web` as its root.
function readModule(path: string): string {
  return readFileSync(join(process.cwd(), path), "utf8");
}

describe.each(MODULES)("%s", (path) => {
  it("declares no fixed pixel width", () => {
    const source = readModule(path);

    const fixed = source.match(/(?<!-)\b(?:inline-size|width)\s*:\s*[^;}]*\b\d+px/gi) ?? [];

    expect(fixed).toEqual([]);
  });

  it("declares no fixed pixel min-width", () => {
    const source = readModule(path);

    const fixed = source.match(/\bmin-(?:inline-size|width)\s*:\s*[^;}]*\b\d+px/gi) ?? [];

    expect(fixed).toEqual([]);
  });

  it("sizes itself from design tokens rather than magic numbers", () => {
    expect(readModule(path)).toMatch(/var\(--/);
  });
});

describe("Container.module.css", () => {
  it("constrains width with max-width so it can still shrink", () => {
    expect(readModule("components/Container.module.css")).toMatch(/max-width\s*:/);
  });
});

describe("app/cards/CardResults.module.css", () => {
  it("makes the whole result row a comfortable touch target", () => {
    // A text-sized hit area is not usable with a thumb, and every row in this
    // list is a link.
    expect(readModule("app/cards/CardResults.module.css")).toMatch(
      /min-block-size\s*:\s*var\(--tap-target\)/,
    );
  });
});

describe("app/identify/page.module.css", () => {
  // Every control on the confirmation gate is one a thumb has to hit, and the
  // two that matter are a decision the user cannot take back by mistake.
  it.each(["confirm", "change", "retry", "record"])(
    "gives .%s a comfortable touch target",
    (name) => {
      const rule = new RegExp(
        String.raw`\.${name}\s*\{[^}]*min-block-size\s*:\s*var\(--tap-target\)`,
      );

      expect(readModule("app/identify/page.module.css")).toMatch(rule);
    },
  );
});

describe("app/analyze/CardUpload.module.css", () => {
  // Every one of these is pressed with a thumb while the other hand holds a
  // card, which is the least forgiving way anything in this product is used.
  it.each(["choose", "remove", "send", "startOver"])(
    "gives .%s a comfortable touch target",
    (name) => {
      const source = readModule("app/analyze/CardUpload.module.css");
      // The four share one rule; the assertion is that the name is in it.
      const shared =
        /\.choose,\s*\n\.remove,\s*\n\.send,\s*\n\.startOver\s*\{[^}]*min-block-size\s*:\s*var\(--tap-target\)/;

      expect(source).toMatch(shared);
      expect(source).toContain(`.${name}`);
    },
  );

  it("keeps the file input reachable by keyboard rather than hiding it", () => {
    const rule = /\.file\s*\{([^}]*)\}/.exec(readModule("app/analyze/CardUpload.module.css"));

    // `display: none` and `visibility: hidden` both take the input out of the
    // tab order, which leaves a control only a mouse can reach. Laying it over
    // its label transparently does not.
    expect(rule?.[1]).toMatch(/opacity\s*:\s*0/);
    expect(rule?.[1]).not.toMatch(/display\s*:\s*none|visibility\s*:\s*hidden/);
  });

  it("lets the preview shrink to the device", () => {
    // A photograph from a phone camera is several thousand pixels wide.
    expect(readModule("app/analyze/CardUpload.module.css")).toMatch(
      /\.previewImage\s*\{[^}]*max-inline-size\s*:\s*100%/,
    );
  });
});
