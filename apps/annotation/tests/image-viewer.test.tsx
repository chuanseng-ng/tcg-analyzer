import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImageViewer } from "@/app/images/[imageId]/ImageViewer";
import { ApiError, type AnnotationImageResponse } from "@/lib/api";

// `ApiError` stays real: the viewer tells a missing image from an outage with
// `instanceof` and the status, exactly as `apps/web`'s gate does.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  readTrainingImage: vi.fn(),
  saveAnnotations: vi.fn(),
  listImagesAwaitingAnnotation: vi.fn(),
}));

// The viewer navigates after a save, so it needs a router. `work-list.test.tsx`
// mocks `next/navigation` the same way and for the same reason.
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const { readTrainingImage, saveAnnotations, listImagesAwaitingAnnotation } =
  await import("@/lib/api");
const readTrainingImageMock = vi.mocked(readTrainingImage);
const saveAnnotationsMock = vi.mocked(saveAnnotations);
const listMock = vi.mocked(listImagesAwaitingAnnotation);

const COPY = "8f14e45f-ceea-467a-9a9d-c1046d0d5a5a";

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    side: "front",
    card_id: null,
    physical_copy_id: COPY,
    source: "first_party",
    created_at: "2026-08-01T10:00:00Z",
    has_artifact: true,
    ...overrides,
  };
}

function image(overrides: Partial<AnnotationImageResponse> = {}): AnnotationImageResponse {
  return {
    ...summary(),
    width: 1200,
    height: 1600,
    siblings: [],
    annotations: [],
    centering: [],
    ...overrides,
  } as AnnotationImageResponse;
}

beforeEach(() => {
  readTrainingImageMock.mockReset();
  saveAnnotationsMock.mockReset();
  listMock.mockReset();
  push.mockReset();
  saveAnnotationsMock.mockResolvedValue({ markers: [], centering: [] });
  listMock.mockResolvedValue({ images: [], total: 0, limit: 1, offset: 0 });
});

describe("front and back", () => {
  it("offers both sides and switches between them", async () => {
    const back = summary({ id: "22222222-2222-4222-8222-222222222222", side: "back" });
    readTrainingImageMock.mockResolvedValue(image({ siblings: [back] }));

    render(<ImageViewer imageId={summary().id} />);

    const toggle = await screen.findByRole("button", { name: "back" });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("front");

    fireEvent.click(toggle);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("back");
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining(encodeURIComponent(back.id)),
    );
  });

  it("says which side is missing rather than showing an empty frame", async () => {
    readTrainingImageMock.mockResolvedValue(image({ siblings: [] }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByText("No other view of this copy has been ingested."),
    ).toBeInTheDocument();
  });

  it("says so when the photograph names no physical copy at all", async () => {
    // The honest degradation, and the one a naive sibling query gets
    // catastrophically wrong: NULL is not a group, so there is no other side to
    // offer — not somebody else's card.
    readTrainingImageMock.mockResolvedValue(image({ physical_copy_id: null, siblings: [] }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByText(
        "This photograph is not linked to a physical copy, so it has no other side.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/no physical copy recorded/)).toBeInTheDocument();
  });
});

describe("which representation is on screen", () => {
  it("labels the normalized artifact, and says coordinates are fractions of it", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: true }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByText(/Normalized artifact — coordinates are fractions of this frame\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("representation=normalized"),
    );
  });

  it("labels a photograph as one, and says coordinates cannot be taken against it", async () => {
    // The whole reason the server answers `has_artifact` rather than letting the
    // client guess: an annotator must never mistake a raw photograph for the
    // space the next issue records coordinates in.
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByText(
        /Original photograph — no card was located, so coordinates cannot be taken against it\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("representation=original"),
    );
  });

  it("names the representation in the alt text too", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByAltText("front of the card — original photograph"),
    ).toBeInTheDocument();
  });
});

describe("the keyboard", () => {
  async function loaded(siblings: ReturnType<typeof summary>[] = []) {
    readTrainingImageMock.mockResolvedValue(image({ siblings }));
    render(<ImageViewer imageId={summary().id} />);
    const frame = await screen.findByRole("group", { name: /pan and zoom/ });
    // The transform only becomes meaningful once the image reports its size.
    const element = screen.getByRole("img");
    Object.defineProperty(element, "naturalWidth", { value: 756, configurable: true });
    Object.defineProperty(element, "naturalHeight", { value: 1056, configurable: true });
    fireEvent.load(element);
    return { frame, element };
  }

  function scaleOf(element: HTMLElement): number {
    const match = /scale\(([\d.]+)\)/.exec(element.style.transform);
    return match ? Number(match[1]) : Number.NaN;
  }

  it("zooms in and out and resets, with no mouse", async () => {
    const { frame, element } = await loaded();
    const fit = scaleOf(element);

    fireEvent.keyDown(frame, { key: "+" });
    await waitFor(() => {
      expect(scaleOf(element)).toBeGreaterThan(fit);
    });

    fireEvent.keyDown(frame, { key: "0" });
    await waitFor(() => {
      expect(scaleOf(element)).toBeCloseTo(fit, 6);
    });

    fireEvent.keyDown(frame, { key: "1" });
    await waitFor(() => {
      expect(scaleOf(element)).toBe(1);
    });
  });

  it("toggles the side with `f`, so front and back never need the mouse", async () => {
    const back = summary({ id: "22222222-2222-4222-8222-222222222222", side: "back" });
    const { frame } = await loaded([back]);

    fireEvent.keyDown(frame, { key: "f" });

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("back");
    });
  });

  it("pans with the arrows rather than navigating", async () => {
    const { frame, element } = await loaded();
    fireEvent.keyDown(frame, { key: "1" }); // zoom in, so panning has somewhere to go
    await waitFor(() => {
      expect(scaleOf(element)).toBe(1);
    });
    const before = element.style.transform;

    fireEvent.keyDown(frame, { key: "ArrowRight" });

    await waitFor(() => {
      expect(element.style.transform).not.toBe(before);
    });
  });

  it("offers every shortcut as a named button as well", async () => {
    await loaded();

    for (const name of ["Zoom in", "Zoom out", "Fit", "Actual size"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("lists the keys on screen rather than hiding them in a tooltip", async () => {
    const { frame } = await loaded();

    expect(frame).toHaveAttribute("aria-describedby", "viewer-keys");
    expect(document.getElementById("viewer-keys")).toBeVisible();
  });
});

describe("failures", () => {
  it("reports a missing image without offering a pointless retry", async () => {
    readTrainingImageMock.mockRejectedValue(new ApiError("gone", { status: 404 }));

    render(<ImageViewer imageId={summary().id} />);

    expect(await screen.findByText("That image is not in the corpus.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("offers a retry when the corpus is simply not answering", async () => {
    readTrainingImageMock.mockRejectedValue(new ApiError("down", { code: "provider_error" }));

    render(<ImageViewer imageId={summary().id} />);

    expect(await screen.findByText("The corpus is not answering right now.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
