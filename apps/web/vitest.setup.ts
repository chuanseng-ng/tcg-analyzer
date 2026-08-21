import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

/*
 * jsdom implements neither half of the object-URL API, and the upload screen
 * previews a staged photograph with it. Defined here rather than in one test so
 * that any component rendering a preview simply works; a test that cares which
 * URLs are still live replaces these with its own spies.
 */
let nextObjectUrl = 0;

URL.createObjectURL = () => `blob:tcg/${String(nextObjectUrl++)}`;
URL.revokeObjectURL = () => {};
