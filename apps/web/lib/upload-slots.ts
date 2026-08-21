/**
 * The two photographs an analysis is built from, while they are still in the
 * browser — spec §11's `front` and `back`, spec §48's upload requirements.
 *
 * Nothing here talks to the network. A slot is staged locally first and only
 * committed when the user says so, which is what makes Retake and Remove
 * possible at all: spec §65's state graph is forward-only (see
 * `packages/domain/src/tcg_domain/analysis.py`), so there is no legal move that
 * takes an analysis back from `uploaded` to `uploading`. A photograph that has
 * not been sent is the only photograph that can be un-chosen.
 *
 * The size and type rules below are a **courtesy**, never enforcement. The
 * server reads the file's actual content and is the only thing that decides
 * (#33, spec §55). Refusing a 40 MB HEIC here saves a user a wasted upload over
 * a mobile connection; it does not make the upload endpoint any safer.
 */

import type { ImageResponse, UploadSide } from "./api";

/**
 * The two sides V1 captures, in the order the screen presents them.
 *
 * `tcg_domain.analysis.ImageSide` admits four more for spec §52's guided
 * photography, and `POST /analyses/{id}/images` deliberately accepts only these
 * two. Ordered front-first because that is the side a person picks up a card by.
 */
export const SIDES = ["front", "back"] as const;

/**
 * The types `services/api/src/tcg_api/analysis/image_validation.py` maps. A
 * third entry here is an upload the server will refuse after receiving all of it.
 */
export const ACCEPTED_MIME_TYPES = ["image/jpeg", "image/png"] as const;

/**
 * The `accept` attribute for the file inputs.
 *
 * Deliberately the concrete types rather than `image/*`. iOS hands over an
 * unconverted HEIC for `image/*`, which the server refuses; naming JPEG makes
 * Safari transcode on the way out. Naming types does **not** hide the camera —
 * Take Photo, Photo Library and Browse are all still offered.
 */
export const ACCEPT_ATTRIBUTE = ACCEPTED_MIME_TYPES.join(",");

/** Mirrors `TCG_API_UPLOAD_MAX_BYTES`, which defaults to 15 MiB. */
export const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

const NOT_AN_IMAGE = "That file is not a JPEG or PNG photograph.";
const EMPTY = "That file contains no data.";

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

/**
 * Why this file cannot be uploaded, or `null` if nothing here objects.
 *
 * Type first, then size: a HEIC that is also too large is more usefully
 * described as the wrong format, because shrinking it would not help.
 *
 * There is deliberately no pixel check. `TCG_API_UPLOAD_MAX_PIXELS` needs the
 * image header, and reading it means decoding the file — which is client-side
 * image analysis, and a non-goal of this screen.
 */
export function rejectionOf(file: File): string | null {
  if (!(ACCEPTED_MIME_TYPES as readonly string[]).includes(file.type)) {
    return NOT_AN_IMAGE;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `That photograph is larger than ${megabytes(MAX_UPLOAD_BYTES)}. Try a smaller one.`;
  }
  if (file.size === 0) {
    return EMPTY;
  }
  return null;
}

/**
 * One side's state.
 *
 * Per-side rather than one state for the pair, so that "the front is stored and
 * the back is not" is representable — which it has to be, because the two
 * uploads are two requests and either can fail alone. A retry then re-sends
 * every side that is not `stored`, which is why there is no `failed` member: a
 * failed upload keeps its photograph and goes back to `staged`, ready to be
 * sent again. The reason it failed belongs to the attempt, not to the file.
 */
export type Slot =
  | { readonly status: "empty" }
  | { readonly status: "staged"; readonly file: File; readonly previewUrl: string }
  | {
      readonly status: "uploading";
      readonly file: File;
      readonly previewUrl: string;
      /** 0 to 1, or `null` when the browser cannot say how much is left. */
      readonly progress: number | null;
    }
  | {
      readonly status: "stored";
      readonly file: File;
      readonly previewUrl: string;
      readonly image: ImageResponse;
    };

/** Both slots, keyed by side. */
export type Slots = Readonly<Record<UploadSide, Slot>>;

export const EMPTY_SLOTS: Slots = { front: { status: "empty" }, back: { status: "empty" } };

/** The file a slot holds, if it holds one. */
export function fileIn(slot: Slot): File | null {
  return slot.status === "empty" ? null : slot.file;
}

/** The preview a slot holds, if it holds one. */
export function previewIn(slot: Slot): string | null {
  return slot.status === "empty" ? null : slot.previewUrl;
}
