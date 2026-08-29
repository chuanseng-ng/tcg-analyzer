import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("labels a photograph as one, and says only surface marks are taken against it", async () => {
    // The whole reason the server answers `has_artifact` rather than letting the
    // client guess: an annotator must never mistake a raw photograph for the
    // space artifact coordinates live in — and since #175 the photograph is a
    // frame of its own, for surface work only.
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));

    render(<ImageViewer imageId={summary().id} />);

    expect(
      await screen.findByText(
        /Original photograph — no card was located, so only surface marks are taken against this frame\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("representation=original"),
    );
  });

  it("offers the original photograph only where an artifact exists to come back to", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: true }));
    render(<ImageViewer imageId={summary().id} />);
    expect(
      await screen.findByRole("button", { name: "View the original photograph" }),
    ).toBeInTheDocument();

    cleanup();

    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));
    render(<ImageViewer imageId={summary().id} />);
    await screen.findByRole("img");
    expect(screen.queryByRole("button", { name: /View the original photograph/ })).toBeNull();
  });

  it("toggles to the original photograph and back, and the frame follows", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: true }));
    render(<ImageViewer imageId={summary().id} />);

    fireEvent.click(await screen.findByRole("button", { name: "View the original photograph" }));

    expect(
      screen.getByText(/Original photograph — only surface defects are marked against this frame\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("representation=original"),
    );
    expect(screen.getByAltText("front of the card — original photograph")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "View the normalized artifact" }));

    expect(
      screen.getByText(/Normalized artifact — coordinates are fractions of this frame\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("representation=normalized"),
    );
  });

  it("arms only pan and surface against the original of an artifact-bearing image", async () => {
    // A corner or edge box taken against the photograph would be stored as a
    // fraction of the artifact, and a centering ratio derived there would be
    // wrong — so the tools that make artifact claims disarm, and come back with
    // the artifact.
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: true }));
    render(<ImageViewer imageId={summary().id} />);

    fireEvent.click(await screen.findByRole("button", { name: "Corner" }));
    expect(screen.getByRole("button", { name: "Corner" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "View the original photograph" }));

    // The armed tool fell back to pan, and the artifact-only tools refuse.
    expect(screen.getByRole("button", { name: "Pan" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Corner" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edge" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Centering" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Surface" })).toBeEnabled();
  });

  it("keeps staged drafts across the toggle — it is the same image", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: true }));
    render(<ImageViewer imageId={summary().id} />);
    await screen.findByRole("img");

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));
    expect(screen.getByRole("button", { name: /^Save 1/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "View the original photograph" }));

    expect(screen.getByRole("button", { name: /^Save 1/ })).toBeInTheDocument();
  });

  it("shows a stored box only over the frame it is a fraction of", async () => {
    // The two frames relate by a projective warp, so a fraction of one means
    // nothing on the other: the overlay filters, and never projects.
    readTrainingImageMock.mockResolvedValue(
      image({
        has_artifact: true,
        annotations: [
          {
            id: "s1",
            kind: "surface",
            region: null,
            label: "scratch",
            severity: "minor",
            confidence: 0.9,
            bbox: { x: 0.4, y: 0.5, width: 0.02, height: 0.01 },
            representation: "original",
            annotator_id: "annotator",
            created_at: "2026-08-29T10:00:00Z",
          },
        ],
      }),
    );
    render(<ImageViewer imageId={summary().id} />);
    const element = await screen.findByRole("img");
    Object.defineProperty(element, "naturalWidth", { value: 756, configurable: true });
    Object.defineProperty(element, "naturalHeight", { value: 1056, configurable: true });
    fireEvent.load(element);

    // On the artifact, the original-photograph box is filtered out…
    expect(screen.queryByText("surface — scratch (saved)")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "View the original photograph" }));

    // …and over the photograph it is drawn.
    expect(screen.getByText("surface — scratch (saved)")).toBeInTheDocument();
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

  it("toggles the representation with `o`, and the artifact-only keys refuse there", async () => {
    const { frame } = await loaded();

    fireEvent.keyDown(frame, { key: "o" });
    await waitFor(() => {
      expect(
        screen.getByText(/Original photograph — only surface defects are marked/),
      ).toBeInTheDocument();
    });

    // `c` arms nothing here — the corner tool is an artifact claim.
    fireEvent.keyDown(frame, { key: "c" });
    expect(screen.getByRole("button", { name: "Corner" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    // `s` still arms the surface tool: the photograph is its frame now.
    fireEvent.keyDown(frame, { key: "s" });
    expect(screen.getByRole("button", { name: "Surface" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
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
