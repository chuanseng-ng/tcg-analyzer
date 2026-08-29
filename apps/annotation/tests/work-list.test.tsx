import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PAGE_SIZE, WorkList } from "@/app/WorkList";
import { ApiError, type AnnotationWorkListResponse } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listImagesAwaitingAnnotation: vi.fn(),
}));

let currentParams = new URLSearchParams();
const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => currentParams,
}));

const { listImagesAwaitingAnnotation } = await import("@/lib/api");
const listMock = vi.mocked(listImagesAwaitingAnnotation);

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    side: "front",
    card_id: null,
    physical_copy_id: null,
    source: "first_party",
    created_at: "2026-08-01T10:00:00Z",
    has_artifact: true,
    ...overrides,
  };
}

function page(overrides: Partial<AnnotationWorkListResponse> = {}): AnnotationWorkListResponse {
  return {
    images: [summary()],
    total: 1,
    limit: PAGE_SIZE,
    offset: 0,
    ...overrides,
  } as AnnotationWorkListResponse;
}

beforeEach(() => {
  listMock.mockReset();
  push.mockReset();
  currentParams = new URLSearchParams();
});

describe("the work list", () => {
  /*
   * Note what this file does *not* claim. Whether an annotated image is
   * excluded is SQL — `NOT EXISTS` against both child tables — and it is
   * asserted against a live database in
   * `services/api/tests/test_annotation_endpoint.py`. Asserting it here would
   * be asserting a mock. What this file owns is that the component renders what
   * it was given and invents nothing.
   */
  it("renders the images the service says are waiting", async () => {
    const second = summary({ id: "22222222-2222-4222-8222-222222222222", side: "back" });
    listMock.mockResolvedValue(page({ images: [summary(), second], total: 2 }));

    render(<WorkList />);

    expect(
      await screen.findByText("2 images with no defect marker and no centering measurement."),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", `/images/${summary().id}`);
  });

  it("says which rows are only a photograph, on every row rather than by omission", async () => {
    const bare = summary({ id: "33333333-3333-4333-8333-333333333333", has_artifact: false });
    listMock.mockResolvedValue(page({ images: [summary(), bare], total: 2 }));

    render(<WorkList />);

    expect(await screen.findByText("photograph only")).toBeInTheDocument();
    expect(screen.getByText("normalized artifact")).toBeInTheDocument();
  });

  it("reads an empty queue as finished, not as broken", async () => {
    listMock.mockResolvedValue(page({ images: [], total: 0 }));

    render(<WorkList />);

    expect(await screen.findByText("Nothing is waiting.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("distinguishes an empty page from an empty queue", async () => {
    currentParams = new URLSearchParams("offset=100");
    listMock.mockResolvedValue(page({ images: [], total: 7 }));

    render(<WorkList />);

    expect(await screen.findByText("This page is past the end of the queue.")).toBeInTheDocument();
  });
});

describe("paging", () => {
  it("asks for the offset in the URL", async () => {
    currentParams = new URLSearchParams("offset=50");
    listMock.mockResolvedValue(page({ images: [summary()], total: 60, offset: 50 }));

    render(<WorkList />);
    await screen.findAllByRole("listitem");

    expect(listMock).toHaveBeenCalledWith(
      { limit: PAGE_SIZE, offset: 50 },
      expect.any(AbortSignal),
    );
  });

  it("carries the offset in the URL rather than in component state", async () => {
    listMock.mockResolvedValue(page({ images: [summary()], total: 60 }));

    render(<WorkList />);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));

    expect(push).toHaveBeenCalledWith(`/?offset=${String(PAGE_SIZE)}`);
  });

  it("does not offer a next page at the end of the queue", async () => {
    listMock.mockResolvedValue(page({ images: [summary()], total: 1 }));

    render(<WorkList />);

    expect(await screen.findByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  });
});

describe("failures", () => {
  it("offers a retry when the corpus is not answering", async () => {
    listMock.mockRejectedValue(new ApiError("down", { code: "provider_error" }));

    render(<WorkList />);

    expect(await screen.findByText("The corpus is not answering right now.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("does not offer a retry for something retrying cannot fix", async () => {
    listMock.mockRejectedValue(new ApiError("bug", { status: 500, code: "internal_error" }));

    render(<WorkList />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong reading the corpus.",
    );
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});
