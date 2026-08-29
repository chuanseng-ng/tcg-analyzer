import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * The tool never reaches an object store directly.
 *
 * ADR 0002 puts every object behind a port, and ADR 0009 says image bytes leave
 * the database's world through `services/api` and nowhere else. That is a
 * *structural* claim about this application, and it has two siblings on the
 * Python side — `packages/shared/tests` keeps `tcg_shared.storage` stdlib-only,
 * and `services/api/tests/test_import_purity.py` keeps the CV stack out of the
 * request path. This is the same guarantee, checked the way a TypeScript
 * application makes it checkable.
 *
 * A source scan rather than an import graph, deliberately: the failure being
 * prevented is somebody *adding a dependency* or pasting a bucket URL, not a
 * lazy import being hoisted. Loading the modules in jsdom would prove less.
 */

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SCANNED = ["app", "components", "lib", "scripts", "styles"];

/** Generated from the OpenAPI schema and formatted by nothing here. */
const GENERATED = new Set(["api-types.ts"]);

function sourcesUnder(directory: string): string[] {
  const found: string[] = [];

  const walk = (path: string) => {
    for (const entry of readdirSync(path)) {
      const full = join(path, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (!GENERATED.has(entry)) found.push(full);
    }
  };

  walk(join(APP_ROOT, directory));
  return found;
}

const SOURCES = SCANNED.flatMap(sourcesUnder);

/**
 * Every way this application could name a store, spelled as it would be spelled.
 * `X-Amz-` and `:9000` are in here because a *pasted signed URL* is the failure
 * that would not add a dependency and would still be the same mistake.
 */
const FORBIDDEN = [
  "@aws-sdk",
  "aws-sdk",
  "amazonaws.com",
  "S3Client",
  "minio",
  "presign",
  "signedUrl",
  "signed_url",
  "s3://",
  "X-Amz-",
  ":9000",
  "TCG_API_STORAGE_",
];

describe("the annotation tool does not reach an object store", () => {
  it("finds the files it claims to be scanning", () => {
    // Guard the guard: a scan that walked nothing would pass every assertion
    // below and prove precisely nothing.
    expect(SOURCES.length).toBeGreaterThan(10);
    expect(SOURCES.some((path) => path.endsWith("api.ts"))).toBe(true);
  });

  it.each(FORBIDDEN)("mentions %s nowhere", (needle) => {
    const offenders = SOURCES.filter((path) => readFileSync(path, "utf8").includes(needle));

    expect(offenders).toEqual([]);
  });

  it("declares no storage dependency", () => {
    const manifest = JSON.parse(readFileSync(join(APP_ROOT, "package.json"), "utf8")) as Record<
      string,
      Record<string, string> | undefined
    >;

    const declared = Object.keys({
      ...manifest.dependencies,
      ...manifest.devDependencies,
    });

    expect(declared.filter((name) => /aws|s3|minio|blob|storage/i.test(name))).toEqual([]);
  });

  it("knows exactly one origin, and it is the API's", () => {
    /*
     * The strongest of these assertions, and the reason the others are cheap
     * keyword checks. A browser bundle cannot reach a store whose address it
     * does not have: `NEXT_PUBLIC_*` values are inlined at build time, so if
     * `lib/env.ts` is the only reader and it reads one variable, there is
     * exactly one host this application can talk to.
     */
    const env = readFileSync(join(APP_ROOT, "lib", "env.ts"), "utf8");
    const reads = env.match(/process\.env\.[A-Z0-9_]+/g) ?? [];

    expect(new Set(reads)).toEqual(new Set(["process.env.NEXT_PUBLIC_API_BASE_URL"]));

    const elsewhere = SOURCES.filter(
      (path) =>
        !path.endsWith(join("lib", "env.ts")) && readFileSync(path, "utf8").includes("process.env"),
    );

    expect(elsewhere).toEqual([]);
  });
});
