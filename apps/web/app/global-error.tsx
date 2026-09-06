"use client";

import "@/styles/tokens.css";
import "@/styles/globals.css";

import { Crashed } from "./Recovery";

/**
 * What the root layout itself throws lands here — Next's `global-error`
 * convention (#261).
 *
 * This replaces the root layout when it renders, so it carries its own
 * `<html>` and `<body>` and imports the two global stylesheets the layout
 * would have. It says exactly what `error.tsx` says and shows nothing from
 * the error, for the same reasons. Next renders it in production builds
 * only; in development its overlay takes the place of both boundaries.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- handed over by Next, deliberately unread
export default function GlobalError(_props: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <Crashed />
      </body>
    </html>
  );
}
