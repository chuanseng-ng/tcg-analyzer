import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportGrade } from "@/app/feedback/[code]/ReportGrade";

/**
 * Spec §68's return screen (#274).
 *
 * Two things are worth pinning here beyond the happy path. The grade options
 * are the chosen company's own ladder, because a client copy of a scale is what
 * puts a 9.5 in front of a PSA submitter; and the ladder the chart draws is the
 * company's scale rather than the stored document's key order, because the
 * stored form is a `{grade: probability}` mapping and JavaScript sorts
 * integer-like keys first — `"10"` before `"1.5"`.
 */

const CODE = "A3KDM-9F2QT-BXWR7-N0HJ5";

const COMPANIES = {
  companies: [
    { company: "psa", display_name: "PSA", grades: ["1", "1.5", "9", "10"], rules: null },
    { company: "bgs", display_name: "BGS", grades: ["1", "9", "9.5", "10"], rules: null },
  ],
};

/** The worker's document (#227), verbatim: an envelope around a slug-keyed map. */
function snapshot(predictions: Record<string, unknown>) {
  return {
    card_id: "44444444-4444-4444-4444-444444444444",
    predictions: { version: "grading-psa-heuristic-v0.1.0", thresholds: {}, predictions },
    recommended_action: "insufficient_information",
    model_bundle_version: "condition-compose-v0.1.0",
    grading_rules_version: "psa-rules-2026-08-24",
    created_at: "2026-09-08T09:15:00Z",
    expires_at: "2027-03-07T09:15:00Z",
  };
}

const PREDICTED = snapshot({
  // Written in the order a JSON object hands back in JavaScript, which is not
  // the ladder: the integer-like keys come first.
  psa: {
    distribution: { "10": 0.1, "1": 0.2, "9": 0.4, "1.5": 0.3 },
    model_confidence: 0.35,
    model_version: "grading-psa-heuristic-v0.1.0",
  },
  bgs: { insufficient_information: "no_condition_assessment" },
});

const ANSWER = {
  status: "submitted",
  submitted_at: "2026-11-02T10:00:00Z",
  grading_company: "psa",
  grade: "9",
  designation: null,
  certification_number: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Routes by path, because the screen makes two reads before it can render. */
function serve(overrides: { feedback?: () => Response; answer?: () => Response } = {}) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const { pathname } = new URL(url);
    if (pathname === "/grading-companies") return Promise.resolve(jsonResponse(COMPANIES));
    if (pathname === `/feedback/${CODE}` && init?.method === "POST") {
      return Promise.resolve((overrides.answer ?? (() => jsonResponse(ANSWER)))());
    }
    if (pathname === `/feedback/${CODE}`) {
      return Promise.resolve((overrides.feedback ?? (() => jsonResponse(PREDICTED)))());
    }
    throw new Error(`unexpected request to ${pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function gradeOptions(): string[] {
  const select = screen.getByLabelText(/the grade on the slab/i);
  return within(select)
    .getAllByRole("option")
    .map((option) => option.textContent ?? "");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ReportGrade", () => {
  it("shows what was predicted, in the company's ladder order", async () => {
    serve();
    render(<ReportGrade code={CODE} />);

    const chart = await screen.findByRole("figure", { name: /PSA grade probabilities/i });
    const grades = within(chart)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent);
    // The scale's order, not the document's — which would have read 1, 9, 10, 1.5.
    expect(grades).toEqual(["1", "1.5", "9", "10"]);
  });

  it("says why a company's model refused, in that model's own words", async () => {
    serve();
    render(<ReportGrade code={CODE} />);

    await screen.findByRole("figure", { name: /PSA grade probabilities/i });
    // No chart for BGS: a refusal has no distribution to draw.
    expect(screen.queryByRole("figure", { name: /BGS/i })).toBeNull();
    expect(screen.getByText(/no_condition_assessment|condition/i)).toBeTruthy();
  });

  it("offers only the grades the chosen company issues", async () => {
    serve();
    render(<ReportGrade code={CODE} />);

    await screen.findByLabelText(/the grade on the slab/i);
    expect(gradeOptions()).toEqual(["1", "1.5", "9", "10"]);

    // BGS issues a 9.5 and PSA does not. A client copy of either ladder is what
    // this is written to make impossible.
    fireEvent.change(screen.getByLabelText(/which company graded it/i), {
      target: { value: "bgs" },
    });
    expect(gradeOptions()).toEqual(["1", "9", "9.5", "10"]);
  });

  it("records the answer and says it back from the response", async () => {
    const fetchMock = serve();
    render(<ReportGrade code={CODE} />);

    await screen.findByLabelText(/the grade on the slab/i);
    fireEvent.change(screen.getByLabelText(/the grade on the slab/i), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: /send the grade/i }));

    expect(await screen.findByRole("heading", { name: /recorded/i })).toBeTruthy();
    expect(screen.getByText(/PSA 9/)).toBeTruthy();

    const posted = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({
      grading_company: "psa",
      grade: "9",
    });
    // A spent code is a 404: the confirmation is the POST's own body, and
    // nothing re-reads the snapshot afterwards.
    expect(
      fetchMock.mock.calls.filter(([url]) => new URL(String(url)).pathname === `/feedback/${CODE}`),
    ).toHaveLength(2);
  });

  it("omits a blank certification number rather than sending an empty one", async () => {
    const fetchMock = serve();
    render(<ReportGrade code={CODE} />);

    await screen.findByLabelText(/the grade on the slab/i);
    fireEvent.click(screen.getByRole("button", { name: /send the grade/i }));

    const posted = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((posted?.[1] as RequestInit).body))).not.toHaveProperty(
      "certification_number",
    );
  });

  it("tells a dead code the four reasons rather than picking one", async () => {
    serve({ feedback: () => jsonResponse({ detail: "no" }, 404) });
    render(<ReportGrade code={CODE} />);

    expect(await screen.findByRole("heading", { name: /no prediction is recorded/i })).toBeTruthy();
    // Unknown, expired, answered and mistyped arrive identically, so all four
    // are named and none is claimed.
    const reasons = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(reasons).toHaveLength(4);
    expect(reasons.join(" ")).toMatch(/expired/i);
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
  });

  it("falls into the same screen when the code is spent between reading and answering", async () => {
    serve({ answer: () => jsonResponse({ detail: "no" }, 404) });
    render(<ReportGrade code={CODE} />);

    await screen.findByLabelText(/the grade on the slab/i);
    fireEvent.click(screen.getByRole("button", { name: /send the grade/i }));

    expect(await screen.findByRole("heading", { name: /no prediction is recorded/i })).toBeTruthy();
  });

  it("offers a retry when the service simply did not answer", async () => {
    serve({
      feedback: () => {
        throw new Error("offline");
      },
    });
    render(<ReportGrade code={CODE} />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });

  it("never renders the code itself", async () => {
    serve();
    const { container } = render(<ReportGrade code={CODE} />);

    await screen.findByLabelText(/the grade on the slab/i);
    // It is a bearer capability. It travels in the path and belongs nowhere in
    // the document, where a screenshot or a support ticket would carry it on.
    expect(container.textContent ?? "").not.toContain(CODE);
  });
});
