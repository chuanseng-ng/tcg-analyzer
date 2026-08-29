import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  classifyAnnotationFailure,
  FAILURE_MESSAGE,
  isWorthRetrying,
} from "@/lib/annotation-errors";

describe("classifyAnnotationFailure", () => {
  it("reads a 404 as missing, because this surface answers a bare one", () => {
    // The internal routes answer FastAPI's own 404 rather than a §66 envelope,
    // on `GET /analyses/{id}`'s reasoning — none of the eight codes means "not
    // found". So this is the one branch that reads a status rather than a code.
    expect(classifyAnnotationFailure(new ApiError("gone", { status: 404 }))).toBe("missing");
  });

  it("reads a malformed identifier as missing too", () => {
    // To a reader it is the same fact: the link does not lead to an image. And
    // it is emphatically not worth a retry — a bad identifier fails identically
    // for ever.
    expect(classifyAnnotationFailure(new ApiError("bad", { status: 422 }))).toBe("missing");
  });

  it("reads provider_error as the corpus not answering", () => {
    expect(classifyAnnotationFailure(new ApiError("down", { code: "provider_error" }))).toBe(
      "unreachable",
    );
  });

  it("reads a request that never arrived the same way", () => {
    expect(classifyAnnotationFailure(new ApiError("no network"))).toBe("unreachable");
  });

  it("does not claim to understand anything else", () => {
    expect(
      classifyAnnotationFailure(new ApiError("?", { status: 500, code: "internal_error" })),
    ).toBe("unexpected");
    expect(classifyAnnotationFailure(new TypeError("not ours"))).toBe("unexpected");
  });

  it("keeps a §66 code ahead of the status it arrived with", () => {
    // The route overrides the taxonomy's default status, so the code is what
    // does not move — the rule `apps/web`'s classifiers are built on.
    expect(
      classifyAnnotationFailure(new ApiError("down", { status: 503, code: "provider_error" })),
    ).toBe("unreachable");
  });
});

describe("what the annotator is offered", () => {
  it("offers a retry only where retrying could work", () => {
    expect(isWorthRetrying("unreachable")).toBe(true);
    expect(isWorthRetrying("missing")).toBe(false);
    expect(isWorthRetrying("unexpected")).toBe(false);
  });

  it("has copy for every outcome, so no screen invents its own", () => {
    for (const failure of ["missing", "unreachable", "unexpected"] as const) {
      expect(FAILURE_MESSAGE[failure]).toMatch(/\S/);
    }
  });
});
