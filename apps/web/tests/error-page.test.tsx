import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";

// What a render crash carries: a message, a stack and Next's digest. None of
// it is the user's to read — an exception's text is not this product's voice
// and may name a variable, a URL or a path (#261).
const SECRET = "NEXT_PUBLIC_API_BASE_URL is not an absolute URL";
const DIGEST = "1234567890";

function crash(): Error & { digest?: string } {
  const error: Error & { digest?: string } = new Error(SECRET);
  error.digest = DIGEST;
  return error;
}

describe("the route error boundary", () => {
  it("says the page could not be shown, in the product's own words", () => {
    render(<ErrorPage error={crash()} reset={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "This page could not be shown." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("offers the start as the way back", () => {
    render(<ErrorPage error={crash()} reset={vi.fn()} />);

    expect(screen.getByRole("link", { name: "Back to the start" })).toHaveAttribute("href", "/");
  });

  it("is announced, like every other failure on a screen", () => {
    render(<ErrorPage error={crash()} reset={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("This page could not be shown.");
  });

  it("shows nothing from the error", () => {
    const { container } = render(<ErrorPage error={crash()} reset={vi.fn()} />);

    expect(container.textContent).not.toContain(SECRET);
    expect(container.textContent).not.toContain(DIGEST);
    expect(container.innerHTML).not.toContain(DIGEST);
  });
});

describe("the root error boundary", () => {
  // Rendered to markup rather than into jsdom: it replaces the root layout,
  // so it carries its own `<html>` and `<body>`, which cannot nest inside a
  // test container.
  const markup = () => renderToStaticMarkup(<GlobalError error={crash()} reset={vi.fn()} />);

  it("is a whole document, laid out for a phone", () => {
    // It replaces the root layout, and with it the layout's viewport.
    const html = markup();

    expect(html).toMatch(/^<html lang="en"><head>.*<\/head><body>/);
    expect(html).toContain('<meta name="viewport" content="width=device-width, initial-scale=1"');
    expect(html).toContain("<title>TCG Grading Advisor</title>");
    expect(html).toContain('role="alert"');
  });

  it("says the same as the route boundary, with the same way back", () => {
    const html = markup();

    expect(html).toContain("This page could not be shown.");
    expect(html).toContain('href="/"');
    expect(html).toContain("Back to the start");
  });

  it("shows nothing from the error", () => {
    const html = markup();

    expect(html).not.toContain(SECRET);
    expect(html).not.toContain(DIGEST);
  });
});
