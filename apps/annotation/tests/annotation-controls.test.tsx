import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImageViewer } from "@/app/images/[imageId]/ImageViewer";
import { ApiError, type AnnotationImageResponse } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  readTrainingImage: vi.fn(),
  saveAnnotations: vi.fn(),
  listImagesAwaitingAnnotation: vi.fn(),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const { readTrainingImage, saveAnnotations, listImagesAwaitingAnnotation } =
  await import("@/lib/api");
const readTrainingImageMock = vi.mocked(readTrainingImage);
const saveAnnotationsMock = vi.mocked(saveAnnotations);
const listMock = vi.mocked(listImagesAwaitingAnnotation);

const IMAGE_ID = "11111111-1111-4111-8111-111111111111";
const COPY = "8f14e45f-ceea-467a-9a9d-c1046d0d5a5a";
/** The artifact, so a fraction of it is a round number of pixels. */
const ARTIFACT = { width: 756, height: 1056 };

function image(overrides: Partial<AnnotationImageResponse> = {}): AnnotationImageResponse {
  return {
    id: IMAGE_ID,
    side: "front",
    card_id: null,
    physical_copy_id: COPY,
    source: "first_party",
    created_at: "2026-08-01T10:00:00Z",
    has_artifact: true,
    width: 1200,
    height: 1600,
    siblings: [],
    annotations: [],
    centering: [],
    ...overrides,
  } as AnnotationImageResponse;
}

/**
 * Load the artifact so the frame knows its natural size.
 *
 * jsdom reports a zero `getBoundingClientRect`, so the frame falls back to
 * 640x800 and `fitted` scales the artifact into it — which is what every
 * coordinate below is computed against.
 */
async function ready() {
  const element = await screen.findByRole("img");
  Object.defineProperty(element, "naturalWidth", { value: ARTIFACT.width, configurable: true });
  Object.defineProperty(element, "naturalHeight", { value: ARTIFACT.height, configurable: true });
  fireEvent.load(element);
  return element;
}

/** The transform the frame settles on, read back out of the style it applied. */
function currentView(element: HTMLElement) {
  const transform = element.style.transform;
  const scale = Number(/scale\(([\d.]+)\)/.exec(transform)?.[1] ?? "1");
  const [x, y] = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/
    .exec(transform)
    ?.slice(1)
    .map(Number) ?? [0, 0];
  return { scale, x: x ?? 0, y: y ?? 0 };
}

/** Drag from one fraction of the artifact to another, in frame coordinates. */
function drag(
  layer: Element,
  view: { scale: number; x: number; y: number },
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  const point = (at: { x: number; y: number }) => ({
    clientX: at.x * ARTIFACT.width * view.scale + view.x,
    clientY: at.y * ARTIFACT.height * view.scale + view.y,
  });

  fireEvent.pointerDown(layer, { button: 0, pointerId: 1, ...point(from) });
  fireEvent.pointerMove(layer, { pointerId: 1, ...point(to) });
  fireEvent.pointerUp(layer, { pointerId: 1, ...point(to) });
  // A real browser releases pointer capture after pointerup and fires
  // lostpointercapture — the event that wiped a just-placed box before the
  // abandon path learned to stand down once the drag has ended.
  fireEvent.lostPointerCapture(layer, { pointerId: 1 });
}

/** Click one corner, in artifact fractions — centering's four-point mode. */
function clickCorner(
  layer: Element,
  view: { scale: number; x: number; y: number },
  at: { x: number; y: number },
) {
  const position = {
    clientX: at.x * ARTIFACT.width * view.scale + view.x,
    clientY: at.y * ARTIFACT.height * view.scale + view.y,
  };
  fireEvent.pointerDown(layer, { button: 0, pointerId: 1, ...position });
  fireEvent.pointerUp(layer, { pointerId: 1, ...position });
  fireEvent.lostPointerCapture(layer, { pointerId: 1 });
}

/** Click all four corners of an axis-aligned outline, deliberately out of order. */
function clickOutline(
  layer: Element,
  view: { scale: number; x: number; y: number },
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  clickCorner(layer, view, { x: to.x, y: to.y });
  clickCorner(layer, view, { x: from.x, y: from.y });
  clickCorner(layer, view, { x: to.x, y: from.y });
  clickCorner(layer, view, { x: from.x, y: to.y });
}

function captureLayer(): Element {
  // The one element inside the frame that takes a drag. It exists only while a
  // tool is armed, which is the whole mechanism.
  const layer = document.querySelector("[class*='capture']");
  if (layer === null) throw new Error("no capture layer — is a tool armed?");
  return layer;
}

beforeEach(() => {
  readTrainingImageMock.mockReset();
  saveAnnotationsMock.mockReset();
  listMock.mockReset();
  push.mockReset();
  readTrainingImageMock.mockResolvedValue(image());
  saveAnnotationsMock.mockResolvedValue({ markers: [], centering: [] });
  listMock.mockResolvedValue({ images: [], total: 0, limit: 1, offset: 0 });
});

describe("arming a tool", () => {
  it("is what mounts the drawing layer, and Escape is what unmounts it", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    // Panning is the default and nothing covers the frame.
    expect(document.querySelector("[class*='capture']")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    expect(document.querySelector("[class*='capture']")).not.toBeNull();

    fireEvent.keyDown(await screen.findByRole("group", { name: /pan and zoom/ }), {
      key: "Escape",
    });
    expect(document.querySelector("[class*='capture']")).toBeNull();
  });

  it("has a key for each tool as well as a button", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();
    const frame = await screen.findByRole("group", { name: /pan and zoom/ });

    for (const [key, name] of [
      ["c", "Corner"],
      ["e", "Edge"],
      ["s", "Surface"],
      ["m", "Centering"],
    ] as const) {
      fireEvent.keyDown(frame, { key });
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
      // …and the button exists too, which is #159's rule for every shortcut.
    }
  });
});

describe("placing a marker", () => {
  it("stores the drag as fractions of the artifact", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    drag(captureLayer(), view, { x: 0.1, y: 0.2 }, { x: 0.3, y: 0.5 });

    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "whitening" } });
    fireEvent.click(screen.getByRole("radio", { name: "minor" }));
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));

    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    const call = saveAnnotationsMock.mock.calls[0];
    expect(call?.[0]).toBe(IMAGE_ID);
    const marker = call?.[1].markers?.[0];
    expect(marker?.kind).toBe("corner");
    expect(marker?.bbox?.x).toBeCloseTo(0.1, 6);
    expect(marker?.bbox?.y).toBeCloseTo(0.2, 6);
    expect(marker?.bbox?.width).toBeCloseTo(0.2, 6);
    expect(marker?.bbox?.height).toBeCloseTo(0.3, 6);
  });

  it("offers an edge the labels a corner cannot have", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    expect(screen.queryByRole("option", { name: "rough cut" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edge" }));
    expect(screen.getByRole("option", { name: "rough cut" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "crease" })).toBeNull();
  });

  it("stamps a surface marker with the frame that was on screen", async () => {
    // #175: the marker carries its representation, and it is the viewer's — the
    // form never asks, because the annotator is marking what they are looking at.
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Surface" }));
    drag(captureLayer(), view, { x: 0.4, y: 0.5 }, { x: 0.45, y: 0.55 });
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "scuff" } });
    fireEvent.click(screen.getByRole("radio", { name: "minor" }));
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add surface/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });
    const marker = saveAnnotationsMock.mock.calls[0]?.[1].markers?.[0];
    expect(marker?.kind).toBe("surface");
    expect(marker && "representation" in marker ? marker.representation : null).toBe("normalized");
  });

  it("offers a surface no `clean`, because a clean surface is no rows at all", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Surface" }));

    expect(screen.getByRole("option", { name: "scratch" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "clean" })).toBeNull();
  });

  it("does not need a box — a corner's region already says where it is", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));

    expect(screen.getByText(/no box/)).toBeInTheDocument();
  });
});

describe("what cannot be saved", () => {
  it("refuses to add a defect with no severity", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "whitening" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));

    expect(screen.getByRole("button", { name: /Add corner/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: "moderate" }));
    expect(screen.getByRole("button", { name: /Add corner/ })).toBeEnabled();
  });

  it("refuses to add anything with no confidence, and pre-selects none", async () => {
    // `image_annotations.confidence` is NOT NULL with no server default on
    // purpose. A checked radio would put that default back where the schema can
    // no longer see it, and every row nobody thought about would read as certain.
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    for (const name of ["Sure", "Fairly sure", "Not sure"]) {
      expect(screen.getByRole("radio", { name })).not.toBeChecked();
    }

    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    expect(screen.getByRole("button", { name: /Add corner/ })).toBeDisabled();
  });

  it("greys out severity for a label that asserts no defect", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });

    expect(screen.getByRole("radio", { name: "minor" })).toBeDisabled();
    expect(screen.getByText("Nothing to rate.")).toBeInTheDocument();
  });
});

describe("admitting you cannot tell", () => {
  it("takes one action and needs nothing else filled in", async () => {
    // If marking a corner takes one click and admitting you cannot tell takes
    // three, the corpus fills with confident guesses.
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.click(screen.getByRole("button", { name: "I cannot tell" }));

    expect(screen.getByRole("button", { name: /Add corner/ })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    const marker = saveAnnotationsMock.mock.calls[0]?.[1].markers?.[0];
    expect(marker?.label).toBe("unknown");
    expect(marker?.severity).toBeNull();
  });
});

describe("centering", () => {
  it("derives both ratios from the gap between the outer edge and the inner frame", async () => {
    // Two quadrilaterals, four clicked corners each: the card's outer edge
    // traced against the background margin, then the printed inner frame. The
    // detector's idea of the card edge is not part of the measurement — a few
    // pixels of quad error on a border a few percent wide swings the ratio
    // wildly, which is how a fine card once read 88.7/11.3.
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    // Step 1 — the card's outer edge.
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    // Step 2 — the inner frame. Borders: left 0.16, right 0.24 (0.4 of the
    // pair); top 0.2, bottom 0.2 (0.5).
    clickOutline(captureLayer(), view, { x: 0.26, y: 0.3 }, { x: 0.66, y: 0.7 });

    fireEvent.click(screen.getByRole("radio", { name: "Fairly sure" }));
    fireEvent.click(screen.getByRole("button", { name: "Set the centering" }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    const centering = saveAnnotationsMock.mock.calls[0]?.[1].centering;
    expect(centering?.horizontal).toBeCloseTo(0.4, 6);
    expect(centering?.vertical).toBeCloseTo(0.5, 6);
    expect(centering?.confidence).toBe(0.6);
  });

  it("counts corners towards the outer edge and then asks for the inner frame", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    expect(screen.getByText(/card&#x27;s four corners|card's four corners/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 4/)).toBeInTheDocument();

    clickCorner(captureLayer(), view, { x: 0.1, y: 0.1 });
    expect(screen.getByText(/1 of 4/)).toBeInTheDocument();

    clickCorner(captureLayer(), view, { x: 0.9, y: 0.1 });
    clickCorner(captureLayer(), view, { x: 0.9, y: 0.9 });
    clickCorner(captureLayer(), view, { x: 0.1, y: 0.9 });

    expect(screen.getByText(/inner frame/)).toBeInTheDocument();
    expect(screen.getByText(/0 of 4/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set the centering" })).toBeDisabled();
  });

  it("measures a tilted card the same as a straight one", async () => {
    // The reason four points exist: rotate card and frame together and the
    // borders — and so the ratios — are what they were.
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);
    const rotate = (point: { x: number; y: number }) => {
      const radians = Math.PI / 12;
      return {
        x: 0.5 + (point.x - 0.5) * Math.cos(radians) - (point.y - 0.5) * Math.sin(radians),
        y: 0.5 + (point.x - 0.5) * Math.sin(radians) + (point.y - 0.5) * Math.cos(radians),
      };
    };

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    for (const corner of [
      { x: 0.15, y: 0.15 },
      { x: 0.85, y: 0.15 },
      { x: 0.85, y: 0.85 },
      { x: 0.15, y: 0.85 },
    ]) {
      clickCorner(captureLayer(), view, rotate(corner));
    }
    // The inner frame sits 0.14 in from the left and 0.28 from the right of a
    // 0.7-wide card (a third of the pair), 0.21 from both top and bottom.
    for (const corner of [
      { x: 0.29, y: 0.36 },
      { x: 0.57, y: 0.36 },
      { x: 0.57, y: 0.64 },
      { x: 0.29, y: 0.64 },
    ]) {
      clickCorner(captureLayer(), view, rotate(corner));
    }

    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: "Set the centering" }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    const centering = saveAnnotationsMock.mock.calls[0]?.[1].centering;
    expect(centering?.horizontal).toBeCloseTo(0.14 / 0.42, 5);
    expect(centering?.vertical).toBeCloseTo(0.5, 5);
  });

  it("sends null for an axis the card has no border on", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    clickOutline(captureLayer(), view, { x: 0.26, y: 0.1 }, { x: 0.66, y: 0.9 });

    fireEvent.click(screen.getByRole("checkbox", { name: "Top and bottom" }));
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: "Set the centering" }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    // Never 0.5 for a full-art layout: the absence is the honest answer.
    expect(saveAnnotationsMock.mock.calls[0]?.[1].centering?.vertical).toBeNull();
  });

  it("says so rather than dividing by zero when the frame fills the card", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));

    expect(screen.getByText(/leaves no border/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set the centering" })).toBeDisabled();
  });

  it("starts again from the outer edge when asked", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    clickOutline(captureLayer(), view, { x: 0.26, y: 0.3 }, { x: 0.66, y: 0.7 });

    fireEvent.click(screen.getByRole("button", { name: "Start again" }));

    expect(screen.getByText(/card&#x27;s four corners|card's four corners/)).toBeInTheDocument();
  });
});

describe("saving", () => {
  it("says a save with no defect markers records the side as clean", async () => {
    // The corpus protocol (#181): corners, edges and surfaces without a marker
    // on a saved image are clean — the surface rule, extended. The bar says so
    // at the moment it becomes true, so silence is a choice and not an
    // accident.
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Centering" }));
    clickOutline(captureLayer(), view, { x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 });
    clickOutline(captureLayer(), view, { x: 0.26, y: 0.3 }, { x: 0.66, y: 0.7 });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: "Set the centering" }));

    expect(screen.getByText(/records this side as clean/)).toBeInTheDocument();

    // Stage a defect and the claim withdraws — the side is no longer clean.
    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "whitening" } });
    fireEvent.click(screen.getByRole("radio", { name: "minor" }));
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));

    expect(screen.queryByText(/records this side as clean/)).not.toBeInTheDocument();
  });

  it("writes nothing until the annotator says so, and stages removably meanwhile", async () => {
    // Both tables refuse an UPDATE, so a mistake has to be removable *before* it
    // is written. That is why nothing posts as it is placed.
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));

    expect(saveAnnotationsMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(screen.getByText(/Nothing staged/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Save 0/ })).toBeDisabled();
  });

  it("takes the annotator to the next image awaiting annotation", async () => {
    listMock.mockResolvedValue({
      images: [
        {
          id: "99999999-9999-4999-8999-999999999999",
          side: "back",
          card_id: null,
          physical_copy_id: null,
          source: "first_party",
          created_at: "2026-08-02T10:00:00Z",
          has_artifact: true,
        },
      ],
      total: 1,
      limit: 1,
      offset: 0,
    });

    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/images/99999999-9999-4999-8999-999999999999");
    });
  });

  it("goes back to the work list when the queue is empty", async () => {
    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/");
    });
  });

  it("says the annotation was refused rather than that the image is missing", async () => {
    // `classifyAnnotationFailure` maps a bare 422 to "not in the corpus", which
    // is right for a read and badly wrong for a write: an annotator who forgot a
    // severity must not be told their image does not exist.
    saveAnnotationsMock.mockRejectedValue(new ApiError("refused", { status: 409 }));

    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));

    expect(await screen.findByText(/would not take that annotation/)).toBeInTheDocument();
    expect(screen.queryByText(/not in the corpus/)).toBeNull();
    // Staged work survives a refusal — it is the only copy there is.
    expect(screen.getByRole("button", { name: /^Save 1/ })).toBeInTheDocument();
  });
});

describe("an image with no artifact", () => {
  it("warns before the service has to, because coordinates would be refused", async () => {
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));

    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    drag(captureLayer(), view, { x: 0.1, y: 0.2 }, { x: 0.3, y: 0.5 });
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "clean" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add corner/ }));

    expect(screen.getByText(/no artifact, so nothing staged against one/)).toBeInTheDocument();
  });

  it("takes surface work against the photograph without a warning (#175)", async () => {
    // The same image, but the staged work claims nothing about an artifact: a
    // surface mark on the original photograph is exactly what ADR 0010 says the
    // photograph is for, and the mirror of the gate lets it through.
    readTrainingImageMock.mockResolvedValue(image({ has_artifact: false }));

    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const view = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Surface" }));
    drag(captureLayer(), view, { x: 0.4, y: 0.5 }, { x: 0.42, y: 0.52 });
    fireEvent.change(screen.getByLabelText("What is there"), { target: { value: "scratch" } });
    fireEvent.click(screen.getByRole("radio", { name: "minor" }));
    fireEvent.click(screen.getByRole("radio", { name: "Sure" }));
    fireEvent.click(screen.getByRole("button", { name: /Add surface/ }));

    expect(screen.queryByText(/no artifact, so nothing staged against one/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^Save 1/ }));
    await waitFor(() => {
      expect(saveAnnotationsMock).toHaveBeenCalled();
    });

    const marker = saveAnnotationsMock.mock.calls[0]?.[1].markers?.[0];
    expect(marker?.kind).toBe("surface");
    expect(marker && "representation" in marker ? marker.representation : null).toBe("original");
  });
});

describe("what is already recorded", () => {
  it("shows every row rather than a collapsed current reading", async () => {
    // Append-only: a correction is a newer row, and a surface has as many
    // defects as it has, so no single collapsing rule is right for all three.
    readTrainingImageMock.mockResolvedValue(
      image({
        annotations: [
          {
            id: "a1",
            kind: "corner",
            region: "top_left",
            label: "whitening",
            severity: "minor",
            confidence: 0.8,
            bbox: null,
            representation: "normalized",
            annotator_id: "annotator",
            created_at: "2026-08-29T10:00:00Z",
          },
          {
            id: "a2",
            kind: "corner",
            region: "top_left",
            label: "whitening",
            severity: "severe",
            confidence: 0.9,
            bbox: null,
            representation: "normalized",
            annotator_id: "annotator",
            created_at: "2026-08-29T11:00:00Z",
          },
        ],
      }),
    );

    render(<ImageViewer imageId={IMAGE_ID} />);
    await ready();

    expect(screen.getByText(/minor/)).toBeInTheDocument();
    expect(screen.getByText(/severe/)).toBeInTheDocument();
  });
});

describe("a box drag belongs to the capture layer alone", () => {
  it("does not pan the view underneath — the frame must never steal the drag", async () => {
    // The frame's pan handler captures the pointer on pointerdown. Before the
    // capture layer learned to stop propagation, an armed drag bubbled to it:
    // the frame stole the capture, the view panned under the box, and the
    // layer never saw the pointerup — so releasing never placed anything and
    // every later hover kept rubber-banding. Found by the first real
    // annotation session; jsdom routes no capture, but the pan half of the
    // theft reproduces here.
    render(<ImageViewer imageId={IMAGE_ID} />);
    const element = await ready();
    const before = currentView(element);

    fireEvent.click(screen.getByRole("button", { name: "Corner" }));
    drag(captureLayer(), before, { x: 0.2, y: 0.2 }, { x: 0.6, y: 0.6 });

    expect(currentView(element)).toEqual(before);
  });
});
