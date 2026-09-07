import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appDirectory = dirname(fileURLToPath(import.meta.url));

/**
 * Plain ESM rather than `next.config.ts`: Next 15's TypeScript config loader
 * fails to resolve inside this pnpm workspace.
 *
 * @type {import("next").NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,
  // `.next/standalone` — a `server.js` and only the dependencies the trace
  // below found — is what the `production` stage of this app's Dockerfile
  // ships, and this is where that stage asks for it. It does not affect
  // `next dev`.
  //
  // Behind a variable rather than always on, because producing it means
  // recreating pnpm's symlinked store inside the output, and creating a
  // symlink on Windows needs a privilege an ordinary account does not have:
  // unconditionally, `pnpm build` would fail on a Windows host. The image
  // builds on Linux, so the Dockerfile sets it and the documented host build
  // is unchanged on every platform.
  output: process.env.NEXT_OUTPUT_STANDALONE ? "standalone" : undefined,
  // The repository root, explicitly rather than by inference — inference is
  // what this setting exists to avoid, because the root holds a second
  // (Python) workspace. It has to reach the root: pnpm links every dependency
  // out of the store there, so a trace rooted at this directory leaves
  // `.next/standalone` without them. Consequently the standalone tree mirrors
  // repository-root-relative paths, which is the layout the Dockerfile copies.
  // See ADR 0003's 2026-09-07 addendum.
  outputFileTracingRoot: join(appDirectory, "../.."),
};

export default nextConfig;
