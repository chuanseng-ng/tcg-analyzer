import { defineConfig, devices } from "@playwright/test";

/**
 * The browser journey runs against an already-running Compose stack
 * (`infrastructure/local/docker-compose.yml`): the web application on :3000,
 * the API on :8000 and the real worker behind it. Nothing is started here —
 * the API's CORS list is pinned to :3000, so there is no other origin to use.
 *
 * One project, Chromium, at the 375px viewport every screen's tests name. No
 * device emulation beyond that, no retries: a hand-off that fails once is a
 * hand-off that fails.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  // `/analyze` waits up to 20 s at the gate on top of the worker's real
  // pipeline, and `next dev` compiles each route on its first visit.
  timeout: 180_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: "http://localhost:3000",
    viewport: { width: 375, height: 667 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
