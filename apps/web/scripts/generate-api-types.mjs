/**
 * Generate `lib/api-types.ts` from the FastAPI service's OpenAPI schema.
 *
 * Per `docs/adr/0001-language-boundaries-in-the-monorepo.md` the frontend and
 * backend share no TypeScript package; the schema is the contract. That makes
 * this the only sanctioned way for `apps/web` to learn an API shape, and it is
 * why CI runs the same script with `--check`: a schema change that is not
 * reflected here means the frontend is compiling against a contract the server
 * no longer honours.
 *
 * The schema is produced by building the app in-process, not by starting a
 * server, so nothing binds a port and no database is required.
 *
 *   node scripts/generate-api-types.mjs            # write
 *   node scripts/generate-api-types.mjs --check    # fail if stale
 */

import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..", "..");
const outputFile = join(webRoot, "lib", "api-types.ts");
const check = process.argv.includes("--check");

const BANNER = `/**
 * Generated from the FastAPI OpenAPI schema. Do not edit by hand.
 *
 * Regenerate with: pnpm --filter @tcg/web gen:api-types
 * See docs/adr/0001-language-boundaries-in-the-monorepo.md.
 */

`;

function fail(message) {
  console.error(`\n  ${message}\n`);
  process.exit(1);
}

/**
 * Windows needs the executable's extension: `spawnSync` does no PATHEXT
 * resolution, and `shell: true` would work but makes Node warn that arguments
 * are concatenated rather than escaped. Naming the binary avoids both.
 */
const uv = process.platform === "win32" ? "uv.exe" : "uv";

const schema = spawnSync(uv, ["run", "--project", repoRoot, "tcg-api-schema"], {
  cwd: repoRoot,
  encoding: "utf8",
  // stderr is inherited so the service's own startup log and any traceback
  // reach the terminal instead of being swallowed into a captured buffer.
  stdio: ["ignore", "pipe", "inherit"],
});

if (schema.error) {
  fail("Could not run `uv`. Install uv, or run this from a shell where it is on PATH.");
}
if (schema.status !== 0) {
  fail(
    `\`tcg-api-schema\` exited with ${schema.status}. ` +
      "The API package may not be installed: run `uv sync --all-packages`.",
  );
}
if (!schema.stdout.trim().startsWith("{")) {
  // Guard against a future change that logs to stdout: it would produce a
  // technically-successful run and a silently corrupt schema.
  fail("`tcg-api-schema` did not write JSON to stdout. Something is polluting the stream.");
}

const generated = BANNER + astToString(await openapiTS(JSON.parse(schema.stdout)));

if (check) {
  let existing;
  try {
    existing = readFileSync(outputFile, "utf8");
  } catch {
    fail("lib/api-types.ts does not exist. Run `pnpm --filter @tcg/web gen:api-types`.");
  }
  // Compare with line endings normalised: git may check the file out with CRLF
  // on Windows, and a developer would have no way to act on that difference.
  const normalise = (text) => text.replace(/\r\n/g, "\n");
  if (normalise(existing) !== normalise(generated)) {
    fail(
      "lib/api-types.ts is out of date with the API's OpenAPI schema.\n" +
        "  Run `pnpm --filter @tcg/web gen:api-types` and commit the result.",
    );
  }
  console.log("API types are up to date.");
} else {
  writeFileSync(outputFile, generated, "utf8");
  console.log(`Wrote ${outputFile}`);
}
