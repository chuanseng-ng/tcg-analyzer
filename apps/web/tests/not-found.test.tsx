import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import NotFound from "@/app/not-found";

describe("the not-found page", () => {
  it("says in the product's own words that nothing lives at this address", () => {
    render(<NotFound />);

    expect(
      screen.getByRole("heading", { name: "There is nothing at this address." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    // A wrong address is not an emergency; only a crash is announced.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers the analysis as the way back", () => {
    render(<NotFound />);

    expect(screen.getByRole("link", { name: "Photograph a card" })).toHaveAttribute(
      "href",
      "/analyze",
    );
  });
});
