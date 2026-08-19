import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LandingPage from "@/app/page";

// The API status indicator is a client component that talks to the FastAPI
// service. The landing-copy assertions below are about the page, not about
// connectivity, so the indicator is stubbed to keep them independent.
vi.mock("@/components/ApiStatus", () => ({
  ApiStatus: () => <div data-testid="api-status-stub" />,
}));

describe("landing page", () => {
  it("renders the primary call to action from spec §48", () => {
    render(<LandingPage />);

    expect(screen.getByRole("link", { name: "Analyze a card" })).toBeInTheDocument();
  });

  it("renders the secondary explanation from spec §48 verbatim", () => {
    render(<LandingPage />);

    expect(
      screen.getByText("Estimate condition, likely grades and whether grading is worthwhile."),
    ).toBeInTheDocument();
  });

  it("states that this is not an official grading service", () => {
    render(<LandingPage />);

    expect(screen.getByText(/not an official grading service/i)).toBeInTheDocument();
  });

  it("states that predictions are probabilities rather than grades", () => {
    render(<LandingPage />);

    expect(screen.getByText(/probabilit/i)).toBeInTheDocument();
  });

  it("never claims a guaranteed grade", () => {
    const { container } = render(<LandingPage />);
    const text = container.textContent ?? "";

    // Every mention of a guarantee must be a denial of one. Capturing the
    // preceding words lets the assertion distinguish "never a guaranteed
    // grade" from "guaranteed grades".
    const mentions = text.match(/(?:\S+\s+){0,3}guarantee\w*/gi) ?? [];
    for (const mention of mentions) {
      expect(mention).toMatch(/\b(never|not|no)\b/i);
    }
  });

  it("does not describe itself as authenticating cards", () => {
    const { container } = render(<LandingPage />);
    const text = container.textContent ?? "";

    const mentions = text.match(/(?:\S+\s+){0,3}authenticat\w*/gi) ?? [];
    for (const mention of mentions) {
      expect(mention).toMatch(/\b(never|not|no|nor|doesn't|does)\b/i);
    }
  });

  it("points the call to action at the analysis route", () => {
    render(<LandingPage />);

    expect(screen.getByRole("link", { name: "Analyze a card" })).toHaveAttribute(
      "href",
      "/analyze",
    );
  });

  it("offers the card catalog as a secondary route, not the primary one", () => {
    render(<LandingPage />);

    expect(screen.getByRole("link", { name: "Browse the card catalog" })).toHaveAttribute(
      "href",
      "/cards",
    );
  });

  it("exposes the call to action as a focusable interactive element", () => {
    render(<LandingPage />);
    const cta = screen.getByRole("link", { name: "Analyze a card" });

    cta.focus();

    expect(cta).toHaveFocus();
  });

  it("wraps its content in a main landmark", () => {
    render(<LandingPage />);

    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("renders the API status indicator", () => {
    render(<LandingPage />);

    expect(screen.getByTestId("api-status-stub")).toBeInTheDocument();
  });
});
