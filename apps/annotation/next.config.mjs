import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Plain ESM rather than `next.config.ts`: Next 15's TypeScript config loader
 * fails to resolve inside this pnpm workspace.
 *
 * @type {import("next").NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,
  // The monorepo root holds a second (Python) workspace. Pinning the trace
  // root stops Next from inferring one above `apps/web`.
  outputFileTracingRoot: dirname(fileURLToPath(import.meta.url)),
};

export default nextConfig;
