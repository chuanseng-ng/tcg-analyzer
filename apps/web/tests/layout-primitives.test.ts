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
