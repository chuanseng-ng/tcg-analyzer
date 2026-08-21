import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardUpload } from "@/app/analyze/CardUpload";
import { currentAnalysis } from "@/lib/analysis-session";
import { ApiError, type AnalysisResponse, type ImageResponse } from "@/lib/api";
import { MAX_UPLOAD_BYTES } from "@/lib/upload-slots";

// `ApiError` stays real: the screen tells a refused photograph from a throttled
// connection from an outage by its `status` and spec §66 `code`.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  startAnalysis: vi.fn(),
  uploadImage: vi.fn(),
  runAnalysis: vi.fn(),
}));

// The hand-off to the catalog is the one navigation this screen performs, and
// it must be a tap rather than something that happens on its own — so the push
// is recorded rather than stubbed away.
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const { startAnalysis, uploadImage, runAnalysis } = await import("@/lib/api");
const startAnalysisMock = vi.mocked(startAnalysis);
const uploadImageMock = vi.mocked(uploadImage);
const runAnalysisMock = vi.mocked(runAnalysis);

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

function analysis(): AnalysisResponse {
  return {
    id: ANALYSIS_ID,
    status: "created",
    created_at: "2026-08-21T00:00:00Z",
    completed_at: null,
    card_id: null,
  };
}

function image(side: string, analysisStatus: string): ImageResponse {
  return {
    id: `44444444-4444-4444-4444-44444444444${side === "front" ? "1" : "2"}`,
    analysis_id: ANALYSIS_ID,
    side,
    mime_type: "image/jpeg",
    sha256: "a".repeat(64),
    created_at: "2026-08-21T00:00:01Z",
    analysis_status: analysisStatus,
  };
}

function photograph(name = "card.jpg", type = "image/jpeg", size = 2048): File {
  return new File([new Uint8Array(size)], name, { type });
}

/** The file input for one side, found the way a user reaches it — by its label. */
function inputFor(side: "front" | "back"): HTMLInputElement {
  return screen.getByLabelText(new RegExp(`(Add|Retake) the ${side}`));
}

function choose(side: "front" | "back", file: File) {
  fireEvent.change(inputFor(side), { target: { files: [file] } });
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Use these photographs|Send the rest|Sending/ });
}

beforeEach(() => {
  window.sessionStorage.clear();
  push.mockReset();
  runAnalysisMock.mockReset();
  runAnalysisMock.mockResolvedValue({ analysis_id: ANALYSIS_ID, status: "queued" });
  startAnalysisMock.mockReset();
  uploadImageMock.mockReset();
  startAnalysisMock.mockResolvedValue(analysis());
  uploadImageMock.mockImplementation(({ side }) =>
    Promise.resolve(image(side, side === "front" ? "uploading" : "uploaded")),
  );
});

describe("the two slots", () => {
  it("names each side in its heading, its control and its status", () => {
    render(<CardUpload />);

    expect(screen.getByRole("heading", { name: "Front of the card" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Back of the card" })).toBeInTheDocument();
    expect(screen.getByText("Add the front")).toBeInTheDocument();
    expect(screen.getByText("Add the back")).toBeInTheDocument();
  });

  it("offers the camera and the file picker through one native control", () => {
    render(<CardUpload />);

    const input = inputFor("front");
    expect(input.type).toBe("file");
    // Naming the concrete types keeps iOS from handing over an unconverted
    // HEIC the server would refuse after the whole upload.
    expect(input.accept).toBe("image/jpeg,image/png");
    // `capture` would force the camera and remove Photo Library and Browse.
    expect(input.hasAttribute("capture")).toBe(false);
  });

  it("previews a chosen photograph with the side in its alt text", () => {
    render(<CardUpload />);

    choose("front", photograph());

    const preview = screen.getByRole("img", { name: /front of your card/i });
    expect(preview).toHaveAttribute("src", expect.stringContaining("blob:"));
  });

  it("leaves the other side untouched — a retake must replace the right one", () => {
    render(<CardUpload />);

    choose("front", photograph("front.jpg"));

    expect(screen.getByRole("img", { name: /front of your card/i })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /back of your card/i })).toBeNull();
    expect(screen.getByText("Add the back")).toBeInTheDocument();
    // The front's control now offers a retake rather than a first choice.
    expect(screen.getByText("Retake the front")).toBeInTheDocument();
  });

  it("replaces only the retaken side's preview", () => {
    render(<CardUpload />);

    choose("front", photograph("first.jpg"));
    choose("back", photograph("back.jpg"));
    const backSrc = screen.getByRole("img", { name: /back of your card/i }).getAttribute("src");

    choose("front", photograph("second.jpg"));

    expect(screen.getByRole("img", { name: /back of your card/i })).toHaveAttribute("src", backSrc);
  });

  it("clears the preview when a staged photograph is removed", () => {
    render(<CardUpload />);
    choose("front", photograph());

    fireEvent.click(screen.getByRole("button", { name: "Remove the front" }));

    expect(screen.queryByRole("img", { name: /front of your card/i })).toBeNull();
    expect(screen.getByText("Add the front")).toBeInTheDocument();
  });
});

describe("the courtesy pre-check", () => {
  it("refuses a file the server would refuse, without sending it", () => {
    render(<CardUpload />);

    choose("front", photograph("photo.heic", "image/heic"));

    expect(screen.getByRole("alert")).toHaveTextContent(/JPEG or PNG/);
    expect(screen.queryByRole("img", { name: /front of your card/i })).toBeNull();
    expect(uploadImageMock).not.toHaveBeenCalled();
  });

  it("refuses a photograph over the byte limit", () => {
    render(<CardUpload />);

    choose("front", photograph("huge.jpg", "image/jpeg", MAX_UPLOAD_BYTES + 1));

    expect(screen.getByRole("alert")).toHaveTextContent(/larger than/i);
  });
});

describe("sending", () => {
  it("sends nothing until both sides are chosen", () => {
    render(<CardUpload />);
    expect(sendButton()).toBeDisabled();

    choose("front", photograph());
    expect(sendButton()).toBeDisabled();

    choose("back", photograph());
    expect(sendButton()).toBeEnabled();
  });

  it("says that nothing has left the device before the user sends", () => {
    render(<CardUpload />);
    choose("front", photograph());

    expect(screen.getAllByText(/Nothing has left this device/)[0]).toBeInTheDocument();
    expect(startAnalysisMock).not.toHaveBeenCalled();
  });

  it("opens one analysis and uploads both sides", async () => {
    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());

    fireEvent.click(sendButton());

    await screen.findByText("Both photographs are stored.");
    expect(startAnalysisMock).toHaveBeenCalledTimes(1);
    expect(uploadImageMock.mock.calls.map(([request]) => request.side)).toEqual(["front", "back"]);
    expect(uploadImageMock.mock.calls.map(([request]) => request.analysisId)).toEqual([
      ANALYSIS_ID,
      ANALYSIS_ID,
    ]);
  });

  it("reports how much of a photograph has been sent", async () => {
    let report: ((fraction: number | null) => void) | undefined;
    uploadImageMock.mockImplementationOnce(({ onProgress }) => {
      report = onProgress;
      return new Promise(() => {});
    });

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    await waitFor(() => expect(report).toBeDefined());
    report?.(0.42);

    expect(await screen.findByText(/42% sent/)).toBeInTheDocument();
  });
});

describe("when one side fails", () => {
  it("keeps the stored side and re-sends only the one that failed", async () => {
    uploadImageMock
      .mockImplementationOnce(() => Promise.resolve(image("front", "uploading")))
      .mockImplementationOnce(() =>
        Promise.reject(new ApiError("dev", { status: 503, code: "provider_error" })),
      );

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(/not answering/i);
    expect(screen.getByText("The front is stored.")).toBeInTheDocument();

    uploadImageMock.mockImplementation(({ side }) => Promise.resolve(image(side, "uploaded")));
    fireEvent.click(sendButton());

    await screen.findByText("Both photographs are stored.");
    // One analysis for the whole run, and the front is not sent twice.
    expect(startAnalysisMock).toHaveBeenCalledTimes(1);
    expect(uploadImageMock.mock.calls.map(([request]) => request.side)).toEqual([
      "front",
      "back",
      "back",
    ]);
  });

  it("keeps the photograph, so the user does not retake it after an outage", async () => {
    uploadImageMock.mockRejectedValue(new ApiError("dev"));

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    await screen.findByRole("alert");
    expect(screen.getByRole("img", { name: /front of your card/i })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /back of your card/i })).toBeInTheDocument();
  });

  it("offers a wait rather than a retry when throttled", async () => {
    startAnalysisMock.mockRejectedValue(
      new ApiError("dev", { status: 429, retryAfterSeconds: 12 }),
    );

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Too many uploads/);
    // ADR 0005: a button here fires straight back into the limit that produced
    // the 429, so the wait is counted down and sending is disabled meanwhile.
    expect(alert).toHaveTextContent(/12 more seconds/);
    await waitFor(() => expect(sendButton()).toBeDisabled());
  });

  it("asks for a fresh analysis when this one can no longer take photographs", async () => {
    uploadImageMock.mockRejectedValue(new ApiError("dev", { status: 409 }));

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(/already moved on/);
    expect(screen.getByRole("button", { name: "Start over" })).toBeInTheDocument();
  });

  it("shows the service's own words for a photograph it refused", async () => {
    uploadImageMock.mockRejectedValue(
      new ApiError("dev", {
        status: 400,
        code: "invalid_image",
        serverMessage: "The image could not be decoded.",
      }),
    );

    render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("The image could not be decoded.");
  });
});

describe("once both photographs are stored", () => {
  async function storeBoth(): Promise<HTMLElement> {
    const { container } = render(<CardUpload />);
    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());
    await screen.findByText("Both photographs are stored.");
    return container;
  }

  it("leads to the catalog, and only on a tap", async () => {
    const container = await storeBoth();

    // Still no link out: the hand-off runs the analysis first, so it cannot be
    // an anchor that navigates before the request has been made.
    expect(container.querySelector("a[href]")).toBeNull();
    expect(push).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Choose which card this is" }));

    await waitFor(() => {
      expect(runAnalysisMock).toHaveBeenCalledWith(ANALYSIS_ID);
    });
    expect(push).toHaveBeenCalledWith("/cards");
  });

  it("leaves the analysis where /identify will look for it", async () => {
    await storeBoth();

    // `/identify` is three screens away, so the identifier travels in the tab
    // rather than through the catalog's URLs.
    expect(currentAnalysis()).toBe(ANALYSIS_ID);
  });

  it("goes on to the catalog when the analysis has already been run", async () => {
    await storeBoth();
    runAnalysisMock.mockRejectedValue(new ApiError("conflict", { status: 409 }));

    fireEvent.click(screen.getByRole("button", { name: "Choose which card this is" }));

    // A 409 means it is already past `uploaded` — which is where this button was
    // trying to get it. Nothing has gone wrong.
    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/cards");
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("stays put and says so when the analysis cannot be run", async () => {
    await storeBoth();
    runAnalysisMock.mockRejectedValue(new ApiError("down", { status: undefined }));

    fireEvent.click(screen.getByRole("button", { name: "Choose which card this is" }));

    await screen.findByRole("alert");
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Choose which card this is" })).toBeEnabled();
  });

  it("still offers a retake of either side", async () => {
    await storeBoth();

    expect(screen.getByText("Retake the front")).toBeInTheDocument();
    expect(screen.getByText("Retake the back")).toBeInTheDocument();
  });

  it("re-sends only the retaken side, against the same analysis", async () => {
    await storeBoth();

    choose("front", photograph("better.jpg"));
    fireEvent.click(sendButton());

    await screen.findByText("Both photographs are stored.");
    expect(startAnalysisMock).toHaveBeenCalledTimes(1);
    expect(uploadImageMock.mock.calls.map(([request]) => request.side)).toEqual([
      "front",
      "back",
      "front",
    ]);
  });

  it("starts over with a clean pair of slots and a new analysis", async () => {
    await storeBoth();

    fireEvent.click(screen.getByRole("button", { name: "Start over" }));

    expect(screen.queryByRole("img", { name: /front of your card/i })).toBeNull();
    expect(screen.getByText("Add the front")).toBeInTheDocument();
    expect(screen.getByText("Add the back")).toBeInTheDocument();
    // The abandoned analysis must not be the one `/identify` confirms against.
    expect(currentAnalysis()).toBeNull();

    choose("front", photograph());
    choose("back", photograph());
    fireEvent.click(sendButton());

    await screen.findByText("Both photographs are stored.");
    // A second analysis, because the first one's photographs cannot be removed.
    expect(startAnalysisMock).toHaveBeenCalledTimes(2);
  });
});
