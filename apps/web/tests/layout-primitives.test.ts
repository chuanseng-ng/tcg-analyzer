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
 */

const MODULES = ["Container.module.css", "Stack.module.css"] as const;

// Vitest runs with `apps/web` as its root.
function readModule(name: string): string {
  return readFileSync(join(process.cwd(), "components", name), "utf8");
}

describe.each(MODULES)("%s", (name) => {
  it("declares no fixed pixel width", () => {
    const source = readModule(name);

    const fixed = source.match(/(?<!-)\bwidth\s*:\s*[^;}]*\b\d+px/gi) ?? [];

    expect(fixed).toEqual([]);
  });

  it("declares no fixed pixel min-width", () => {
    const source = readModule(name);

    const fixed = source.match(/\bmin-width\s*:\s*[^;}]*\b\d+px/gi) ?? [];

    expect(fixed).toEqual([]);
  });

  it("sizes itself from design tokens rather than magic numbers", () => {
    expect(readModule(name)).toMatch(/var\(--/);
  });
});

describe("Container.module.css", () => {
  it("constrains width with max-width so it can still shrink", () => {
    expect(readModule("Container.module.css")).toMatch(/max-width\s*:/);
  });
});
