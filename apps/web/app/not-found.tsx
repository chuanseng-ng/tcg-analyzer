import type { Metadata } from "next";

import { Recovery } from "./Recovery";

export const metadata: Metadata = {
  title: "Not found",
};

/**
 * An address nothing answers to — Next's `not-found` convention (#261).
 *
 * Rendered inside the root layout for any URL no route matches, in this
 * product's voice rather than Next's, and pointing at where an analysis
 * begins. It takes no props and reads nothing.
 */
export default function NotFound() {
  return (
    <Recovery
      heading="There is nothing at this address."
      body={
        "Nothing in this product lives here — the address may have been mistyped, or the " +
        "page may have moved. An analysis begins with photographs of a card."
      }
      href="/analyze"
      action="Photograph a card"
    />
  );
}
