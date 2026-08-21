import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyUploadFailure } from "@/lib/upload-errors";

describe("classifyUploadFailure", () => {
  it("shows the service's own words for a refused photograph", () => {
    const failure = classifyUploadFailure(
      new ApiError("dev message", {
        status: 400,
        code: "invalid_image",
        serverMessage: "The image is larger than 15,728,640 bytes.",
      }),
    );

    expect(failure.action).toBe("retake");
    // Paraphrasing would be a guess: this module cannot know which of the byte,
    // pixel, format and decode rules the file tripped.
    expect(failure.message).toBe("The image is larger than 15,728,640 bytes.");
  });

  it("falls back to its own copy when the envelope carried no message", () => {
    const failure = classifyUploadFailure(
      new ApiError("dev", { status: 400, code: "invalid_image" }),
    );

    expect(failure.action).toBe("retake");
    expect(failure.message).not.toBe("dev");
  });

  it("never offers a retry for a throttled upload", () => {
    const failure = classifyUploadFailure(
      new ApiError("dev", { status: 429, retryAfterSeconds: 30 }),
    );

    // ADR 0005: a Retry button here fires straight back into the limit that
    // produced the 429. The wait is the only honest thing to offer.
    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBe(30);
  });

  it("still waits when the service did not say how long", () => {
    const failure = classifyUploadFailure(new ApiError("dev", { status: 429 }));

    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBeUndefined();
  });

  it.each([409, 404])("asks for a fresh analysis after a %d", (status) => {
    // 409: the analysis has moved past taking photographs. 404: the session is
    // gone. Neither is fixed by sending this photograph again.
    expect(classifyUploadFailure(new ApiError("dev", { status })).action).toBe("restart");
  });

  it("treats an unreachable service as worth trying again", () => {
    expect(
      classifyUploadFailure(new ApiError("dev", { status: 503, code: "provider_error" })).action,
    ).toBe("retry");
  });

  it("treats a request that never left as unreachable", () => {
    expect(classifyUploadFailure(new ApiError("dev")).action).toBe("retry");
  });

  it("does not crash on something that is not an ApiError", () => {
    expect(classifyUploadFailure(new TypeError("boom")).action).toBe("retry");
    expect(classifyUploadFailure(undefined).action).toBe("retry");
  });

  it("says nothing was sent when the service is unreachable", () => {
    // The user is holding the card and needs to know whether to re-photograph
    // it or simply wait.
    expect(classifyUploadFailure(new ApiError("dev")).message).toMatch(/not been sent/i);
  });
});
