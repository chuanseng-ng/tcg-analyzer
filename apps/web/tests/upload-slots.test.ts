import { describe, expect, it } from "vitest";

import {
  ACCEPT_ATTRIBUTE,
  ACCEPTED_MIME_TYPES,
  MAX_UPLOAD_BYTES,
  rejectionOf,
} from "@/lib/upload-slots";

/**
 * These rules are a courtesy, not enforcement — the server sniffs the content
 * and is the only thing that decides (#33, spec §55). What these tests protect
 * is that the courtesy stays *narrower* than the server's rule: a file this
 * module accepts may still be refused, but a file this module refuses must be
 * one the server would refuse too. Anything looser silently costs a user a
 * whole upload over a slow connection; anything stricter blocks a valid photo.
 */

function fileOf(type: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], "photo", { type });
}

describe("rejectionOf", () => {
  it.each(ACCEPTED_MIME_TYPES)("accepts %s", (type) => {
    expect(rejectionOf(fileOf(type, 1024))).toBeNull();
  });

  it.each([
    ["image/heic", "an iPhone's native format"],
    ["image/gif", "an image the pipeline cannot read"],
    ["application/pdf", "not an image at all"],
    ["", "a file the browser could not type"],
  ])("refuses %s (%s)", (type) => {
    expect(rejectionOf(fileOf(type, 1024))).toContain("JPEG or PNG");
  });

  it("refuses a file over the byte limit", () => {
    expect(rejectionOf(fileOf("image/jpeg", MAX_UPLOAD_BYTES + 1))).toMatch(/large/i);
  });

  it("accepts a file exactly at the byte limit", () => {
    // The server's own check is `> max_bytes`, so the boundary belongs inside.
    expect(rejectionOf(fileOf("image/jpeg", MAX_UPLOAD_BYTES))).toBeNull();
  });

  it("refuses an empty file", () => {
    expect(rejectionOf(fileOf("image/jpeg", 0))).toMatch(/no data|empty/i);
  });

  it("names the type before the size, so the more useful reason wins", () => {
    expect(rejectionOf(fileOf("image/heic", MAX_UPLOAD_BYTES + 1))).toContain("JPEG or PNG");
  });
});

describe("the file input's contract", () => {
  it("offers exactly the two types the server accepts", () => {
    // `services/api/src/tcg_api/analysis/image_validation.py` maps JPEG and PNG
    // and nothing else. A third entry here is an upload the server will refuse.
    expect(ACCEPTED_MIME_TYPES).toEqual(["image/jpeg", "image/png"]);
  });

  it("mirrors the server's default byte limit", () => {
    // `TCG_API_UPLOAD_MAX_BYTES` defaults to 15 MiB in
    // `services/api/src/tcg_api/config.py`.
    expect(MAX_UPLOAD_BYTES).toBe(15 * 1024 * 1024);
  });

  it("builds an accept attribute naming the concrete types", () => {
    // Deliberately not `image/*`: iOS then hands over HEIC unconverted, which
    // the server refuses after the whole file has been uploaded.
    expect(ACCEPT_ATTRIBUTE).toBe("image/jpeg,image/png");
  });
});
