import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FeedbackOffer } from "@/app/results/FeedbackOffer";

/**
 * Spec §68's offer on `/results` (#274). The claim under test is the one the
 * copy makes to the user: the code is shown once, it lives nowhere but this
 * component, and asking twice says so rather than handing out a second one.
 */

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";
const CODE = "A3KDM-9F2QT-BXWR7-N0HJ5";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function minted() {
  return jsonResponse({ return_code: CODE, expires_at: "2027-03-07T09:15:00Z" }, 201);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FeedbackOffer", () => {
  it("asks before it mints, so nothing is issued to a user who did not want one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(minted());
    vi.stubGlobal("fetch", fetchMock);

    render(<FeedbackOffer analysisId={ANALYSIS_ID} />);

    expect(screen.getByRole("button", { name: /give me a code/i })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the code once, and says it is the only way back", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(minted()));

    render(<FeedbackOffer analysisId={ANALYSIS_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /give me a code/i }));

    expect(await screen.findByText(CODE)).toBeTruthy();
    expect(screen.getByText(/will not be shown again/i)).toBeTruthy();
    // The offer is gone: there is nothing left to ask for.
    expect(screen.queryByRole("button", { name: /give me a code/i })).toBeNull();
  });

  it("keeps the code out of every store that outlives the component", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(minted()));

    const { unmount } = render(<FeedbackOffer analysisId={ANALYSIS_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /give me a code/i }));
    await screen.findByText(CODE);

    // The row stores a sha256 and cannot reproduce the code, so anywhere this
    // page put it would be a copy the service could not revoke.
    expect(window.sessionStorage.getItem("tcg.analysis")).toBeNull();
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(CODE);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(CODE);
    expect(window.location.href).not.toContain(CODE);

    unmount();
  });

  it("says a code was already issued rather than offering a second", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "already minted" }, 409)),
    );

    render(<FeedbackOffer analysisId={ANALYSIS_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /give me a code/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already issued/i);
    // Terminal: pressing again cannot produce the code, so nothing is offered.
    expect(screen.queryByRole("button", { name: /give me a code/i })).toBeNull();
  });

  it("keeps the offer live when the service simply did not answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<FeedbackOffer analysisId={ANALYSIS_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /give me a code/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /give me a code/i })).toBeTruthy();
  });

  it("offers no button while throttled, and counts the wait down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "slow down" }), {
          status: 429,
          headers: { "content-type": "application/json", "retry-after": "20" },
        }),
      ),
    );

    render(<FeedbackOffer analysisId={ANALYSIS_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /give me a code/i }));

    // A button here would fire straight back into the limit (ADR 0005).
    expect(await screen.findByRole("alert")).toHaveTextContent("20s");
    expect(screen.queryByRole("button", { name: /give me a code/i })).toBeNull();
  });
});
