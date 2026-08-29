import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

/*
 * `apps/web`'s object-URL shim is deliberately absent. That app stages a
 * photograph in the browser and previews it with `createObjectURL`; this one
 * never holds bytes at all — an `<img src>` points at the API and the browser
 * fetches it, which is also what `tests/no-object-store.test.ts` relies on.
 * Add the shim back only if something here starts making a blob, and expect
 * that test to have an opinion about why.
 */

/*
 * Pointer capture, which jsdom does not implement.
 *
 * Unlike the shim above, this one is a gap in the test environment rather than a
 * capability the application should not have: every browser implements it, and
 * both the pan gesture and the annotation drag rely on a pointer that keeps
 * reporting after it leaves the element. Without it `fireEvent.pointerDown`
 * throws and no drag can be tested at all.
 *
 * A capture set is recorded so `hasPointerCapture` stays truthful, because a
 * shim that lied about it would hide exactly the stuck-drag bug the release
 * handlers exist to prevent.
 */
const captured = new WeakMap<Element, Set<number>>();

if (!("setPointerCapture" in Element.prototype)) {
  Object.assign(Element.prototype, {
    setPointerCapture(this: Element, pointerId: number) {
      const held = captured.get(this) ?? new Set<number>();
      held.add(pointerId);
      captured.set(this, held);
    },
    releasePointerCapture(this: Element, pointerId: number) {
      captured.get(this)?.delete(pointerId);
    },
    hasPointerCapture(this: Element, pointerId: number) {
      return captured.get(this)?.has(pointerId) ?? false;
    },
  });
}
