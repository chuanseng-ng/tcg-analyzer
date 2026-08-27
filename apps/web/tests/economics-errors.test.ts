import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyCompaniesFailure, classifyConfigureFailure } from "@/lib/economics-errors";

describe("classifyConfigureFailure", () => {
  it("counts a throttled submission down rather than offering a button", () => {
    // ADR 0005: a button here fires straight back into the limit that produced
    // the 429.
    const failure = classifyConfigureFailure(
      new ApiError("throttled", { status: 429, retryAfterSeconds: 12 }),
    );

    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBe(12);
  });

  it("omits the countdown when the service did not say how long", () => {
    const failure = classifyConfigureFailure(new ApiError("throttled", { status: 429 }));

    expect(failure.action).toBe("wait");
    expect(failure).not.toHaveProperty("retryAfterSeconds");
  });

  it("treats a second configuration as final, not as something to retry", () => {
    // #65: the configuration is written once and is immutable. This screen is
    // only reachable after `confirm-card` succeeded, which is what sets
    // `analyzing`, so a 409 is never a worker that has yet to catch up.
    const failure = classifyConfigureFailure(new ApiError("conflict", { status: 409 }));

    expect(failure.action).toBe("gone");
    expect(failure.message).toMatch(/already recorded/i);
  });

  it("says the analysis is gone without guessing which of the four misses it was", () => {
    // #32: unknown id, another session's analysis, no cookie and an expired
    // cookie are one 404 with one body.
    expect(classifyConfigureFailure(new ApiError("missing", { status: 404 })).action).toBe("gone");
  });

  it("asks for another look at the figures when the service refuses them", () => {
    // 422 is FastAPI's own — spec §66 has no code for a malformed request.
    const failure = classifyConfigureFailure(new ApiError("unprocessable", { status: 422 }));

    expect(failure.action).toBe("retry");
    expect(failure.message).toMatch(/figures/i);
  });

  it("reports an unreachable store as an outage rather than a bad form", () => {
    const failure = classifyConfigureFailure(
      new ApiError("down", { status: 503, code: "provider_error" }),
    );

    expect(failure.action).toBe("retry");
    expect(failure.message).toMatch(/not answering/i);
  });

  it("treats a request that never left as an outage too", () => {
    expect(classifyConfigureFailure(new ApiError("no network")).action).toBe("retry");
    expect(classifyConfigureFailure(new TypeError("boom")).message).toMatch(/not answering/i);
  });
});

describe("classifyCompaniesFailure", () => {
  it("tells an outage from an answer this page did not understand", () => {
    expect(
      classifyCompaniesFailure(new ApiError("down", { status: 503, code: "provider_error" })),
    ).toBe("unreachable");
    expect(classifyCompaniesFailure(new ApiError("nonsense", { status: 200 }))).toBe("unexpected");
    expect(classifyCompaniesFailure(new ApiError("no network"))).toBe("unreachable");
  });
});
