/**
 * The client for `services/api`'s internal annotation surface.
 *
 * `apps/web`'s `lib/api.ts` in shape and in reasoning — one `requestJson`
 * wrapper, one hand-written runtime guard per payload, every failure an
 * {@link ApiError} carrying the spec §66 code — and a copy rather than a shared
 * module, for the reason noted on `components/Container.tsx`. Two things are
 * deliberately missing: there is no `credentials`, because this tool has no
 * session and V1 has no accounts; and there is no upload path, because nothing
 * here sends bytes.
 *
 * The types come from `./api-types`, generated from the OpenAPI schema
 * (ADR 0001). Nothing below redeclares a wire shape.
 */

import { apiBaseUrl } from "./env";
import type { components, operations, paths } from "./api-types";

export { apiBaseUrl };

export type AnnotationWorkListResponse = components["schemas"]["AnnotationWorkListResponse"];
export type AnnotationImageResponse = components["schemas"]["AnnotationImageResponse"];
export type AnnotationImageSummary = components["schemas"]["AnnotationImageSummary"];

/**
 * Read off the operation's own query parameter rather than written out here, so
 * a representation the service adds or removes is a compile error in this file
 * instead of a runtime 422 in front of an annotator.
 */
export type Representation = NonNullable<
  NonNullable<
    operations["read_training_image_bytes_internal_annotation_images__image_id__bytes_get"]["parameters"]["query"]
  >["representation"]
>;

/** The work list is a route on the service, so its path is the schema's too. */
type WorkListPath = keyof Pick<paths, "/internal/annotation/images">;

const WORK_LIST: WorkListPath = "/internal/annotation/images";

/**
 * Generous next to `apps/web`'s catalog reads. The corpus is small and local,
 * but an annotator on a slow link is waiting on a person's next action rather
 * than on a page render, and a spurious abort costs more than the wait.
 */
const READ_TIMEOUT_MS = 15_000;

interface ApiErrorOptions {
  readonly status?: number;
  readonly code?: string;
  readonly details?: Record<string, unknown>;
  readonly serverMessage?: string;
  readonly cause?: unknown;
}

export class ApiError extends Error {
  readonly status: number | undefined;
  readonly code: string | undefined;
  readonly details: Record<string, unknown> | undefined;
  readonly serverMessage: string | undefined;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.serverMessage = options.serverMessage;
  }
}

function isRecord(payload: unknown): payload is Record<string, unknown> {
  return typeof payload === "object" && payload !== null;
}

function liftEnvelope(
  payload: unknown,
): Pick<ApiErrorOptions, "code" | "serverMessage" | "details"> {
  if (!isRecord(payload) || typeof payload.code !== "string") {
    return {};
  }

  return {
    code: payload.code,
    ...(typeof payload.message === "string" ? { serverMessage: payload.message } : {}),
    ...(isRecord(payload.details) ? { details: payload.details } : {}),
  };
}

async function readFailure(response: Response): Promise<ApiErrorOptions> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    // A 404 from this surface is FastAPI's own `{detail: …}` rather than a §66
    // envelope, and a proxy in front of it may return no JSON at all. Neither
    // is worth losing the status over.
    return { status: response.status };
  }

  return { status: response.status, ...liftEnvelope(payload) };
}

function requestSignal(
  caller: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal | undefined {
  const deadline =
    typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(timeoutMs) : undefined;

  if (caller === undefined) return deadline;
  if (deadline === undefined) return caller;

  return typeof AbortSignal.any === "function" ? AbortSignal.any([caller, deadline]) : caller;
}

interface JsonRequest<T> {
  readonly url: string;
  readonly signal: AbortSignal | undefined;
  readonly isPayload: (payload: unknown) => payload is T;
  readonly payloadName: string;
}

async function requestJson<T>(request: JsonRequest<T>): Promise<T> {
  const { url, isPayload, payloadName } = request;

  let response: Response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: requestSignal(request.signal, READ_TIMEOUT_MS),
    });
  } catch (cause) {
    throw new ApiError(`The API at ${url} could not be reached.`, { cause });
  }

  if (!response.ok) {
    throw new ApiError(`The API at ${url} returned ${String(response.status)}.`, {
      ...(await readFailure(response)),
    });
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new ApiError(`The API at ${url} returned a malformed response.`, {
      status: response.status,
      cause,
    });
  }

  if (!isPayload(payload)) {
    throw new ApiError(`The API at ${url} returned an unrecognised ${payloadName} payload.`, {
      status: response.status,
    });
  }

  return payload;
}

function isImageSummary(payload: unknown): payload is AnnotationImageSummary {
  return (
    isRecord(payload) &&
    typeof payload.id === "string" &&
    typeof payload.side === "string" &&
    typeof payload.source === "string" &&
    typeof payload.created_at === "string" &&
    typeof payload.has_artifact === "boolean"
  );
}

function isWorkList(payload: unknown): payload is AnnotationWorkListResponse {
  return (
    isRecord(payload) &&
    Array.isArray(payload.images) &&
    payload.images.every(isImageSummary) &&
    typeof payload.total === "number" &&
    typeof payload.limit === "number" &&
    typeof payload.offset === "number"
  );
}

function isImage(payload: unknown): payload is AnnotationImageResponse {
  return (
    isImageSummary(payload) &&
    typeof (payload as Record<string, unknown>).width === "number" &&
    typeof (payload as Record<string, unknown>).height === "number" &&
    ((payload as Record<string, unknown>).representation === "normalized" ||
      (payload as Record<string, unknown>).representation === "original") &&
    Array.isArray((payload as Record<string, unknown>).siblings) &&
    ((payload as Record<string, unknown>).siblings as unknown[]).every(isImageSummary)
  );
}

/** `GET /internal/annotation/images` — the images nobody has annotated yet. */
export async function listImagesAwaitingAnnotation(
  { limit, offset }: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<AnnotationWorkListResponse> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });

  return requestJson({
    url: `${apiBaseUrl()}${WORK_LIST}?${query.toString()}`,
    signal,
    isPayload: isWorkList,
    payloadName: "work list",
  });
}

/** `GET /internal/annotation/images/{id}` — one image and the other views of its copy. */
export async function readTrainingImage(
  imageId: string,
  signal?: AbortSignal,
): Promise<AnnotationImageResponse> {
  return requestJson({
    url: `${apiBaseUrl()}${WORK_LIST}/${encodeURIComponent(imageId)}`,
    signal,
    isPayload: isImage,
    payloadName: "training image",
  });
}

/**
 * The URL an `<img>` loads. Not a fetch, deliberately.
 *
 * The browser makes the request, honours `Cache-Control: private, no-store` and
 * hands the decoded pixels to the element. Fetching the bytes into script and
 * wrapping them in an object URL would put a copy of a training photograph in
 * this application's memory for no benefit — and would need the object-URL
 * shim `vitest.setup.ts` deliberately does not carry.
 */
export function imageBytesUrl(imageId: string, representation: Representation): string {
  const query = new URLSearchParams({ representation });

  return `${apiBaseUrl()}${WORK_LIST}/${encodeURIComponent(imageId)}/bytes?${query.toString()}`;
}
