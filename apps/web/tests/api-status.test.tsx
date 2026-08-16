import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiStatus } from "@/components/ApiStatus";
import { ApiError, getHealth } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getHealth: vi.fn() };
});

const getHealthMock = vi.mocked(getHealth);

beforeEach(() => {
  getHealthMock.mockReset();
});

describe("ApiStatus", () => {
  it("announces its state politely to assistive technology", async () => {
    getHealthMock.mockResolvedValue({
      status: "ok",
      application_version: "0.0.0",
    });

    render(<ApiStatus />);
    const status = await screen.findByRole("status");

    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("reports the application version once the API answers", async () => {
    getHealthMock.mockResolvedValue({
      status: "ok",
      application_version: "0.0.0",
    });

    render(<ApiStatus />);

    expect(await screen.findByText(/0\.0\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/reachable/i)).toBeInTheDocument();
  });

  it("renders an unreachable state instead of throwing when the API is down", async () => {
    getHealthMock.mockRejectedValue(new ApiError("Network failure"));

    render(<ApiStatus />);

    expect(await screen.findByText(/unreachable/i)).toBeInTheDocument();
  });

  it("survives a rejection that is not an ApiError", async () => {
    getHealthMock.mockRejectedValue(new Error("something else entirely"));

    render(<ApiStatus />);

    expect(await screen.findByText(/unreachable/i)).toBeInTheDocument();
  });

  it("shows a checking state before the API answers", () => {
    getHealthMock.mockReturnValue(new Promise(() => {}));

    render(<ApiStatus />);

    expect(screen.getByText(/checking/i)).toBeInTheDocument();
  });
});
