"use client";

import { Crashed } from "./Recovery";

/**
 * What a route segment throws lands here — Next's `error` convention (#261).
 *
 * Next hands the boundary the error and a `reset`, and this shows neither:
 * an exception's text is not this product's voice, and it may name a
 * variable, a path or a URL (`lib/env.ts` refusing a malformed base URL puts
 * the value in its message). The one way out is the start, not `reset`,
 * because re-rendering the segment that just threw rarely helps and a link
 * back into `/analyze` would loop if the crash was there. Nothing is logged
 * from the client; Next already reports the error on the server.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- handed over by Next, deliberately unread
export default function ErrorPage(_props: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  return <Crashed />;
}
