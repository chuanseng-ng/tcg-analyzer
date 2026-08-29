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
