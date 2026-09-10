import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WithdrawConsent } from "@/app/consent/WithdrawConsent";
import { ApiError } from "@/lib/api";

// `ApiError` stays real: the screen tells a code that addresses nothing from a
// throttled connection from an outage by its `status`.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getConsentText: vi.fn(),
  withdrawTrainingConsent: vi.fn(),
}));

const { getConsentText, withdrawTrainingConsent } = await import("@/lib/api");
const getConsentTextMock = vi.mocked(getConsentText);
const withdrawMock = vi.mocked(withdrawTrainingConsent);

const CODE = "A3KDM-9F2QT-BXWR7-N0HJ5";

function codeBox(): HTMLInputElement {
  return screen.getByLabelText("Your code");
}

function withdrawButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Withdraw/ });
}

beforeEach(() => {
  getConsentTextMock.mockReset();
  getConsentTextMock.mockResolvedValue({
    version: "user-upload-consent-v1.0.0",
    paragraphs: ["A model trained on a photograph is something derived from it."],
  });
  withdrawMock.mockReset();
  withdrawMock.mockResolvedValue({ deleted: 2, kept: 0 });
});

describe("the withdrawal screen", () => {
  it("asks for the code and reads nothing until the button is pressed", async () => {
    render(<WithdrawConsent />);

    await screen.findByLabelText("Your code");

    // There is no route that looks a code up, deliberately: one would let
    // somebody holding a guess learn it was real without spending it.
    expect(withdrawMock).not.toHaveBeenCalled();
    expect(withdrawButton().disabled).toBe(true);
  });

  it("shows what was agreed to, in the server's words", async () => {
    render(<WithdrawConsent />);

    expect(
      await screen.findByText("A model trained on a photograph is something derived from it."),
    ).toBeTruthy();
  });

  it("withdraws on the code the user typed, trimmed", async () => {
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), {
      target: { value: `  ${CODE} ` },
    });

    fireEvent.click(withdrawButton());

    expect(withdrawMock).toHaveBeenCalledWith(CODE);
    expect(await screen.findByRole("heading", { name: "2 photographs are gone." })).toBeTruthy();
  });

  it("renders the confirmation from the response, because the code is spent", async () => {
    withdrawMock.mockResolvedValue({ deleted: 1, kept: 1 });
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: CODE } });

    fireEvent.click(withdrawButton());

    await screen.findByRole("heading", { name: "1 photograph is gone." });
    // §31 makes a published version immutable, and the consent text said so
    // before anybody agreed — so this is a fact, not a failure.
    expect(screen.getByText(/already inside a published training set/)).toBeTruthy();
    // Nothing re-reads a spent code.
    expect(withdrawMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Your code")).toBeNull();
  });

  it("says so when every photograph was already frozen", async () => {
    withdrawMock.mockResolvedValue({ deleted: 0, kept: 2 });
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: CODE } });

    fireEvent.click(withdrawButton());

    expect(
      await screen.findByRole("heading", { name: "Nothing was left to delete." }),
    ).toBeTruthy();
  });

  it("names three reasons a code stops working and claims none of them", async () => {
    withdrawMock.mockRejectedValue(new ApiError("missing", { status: 404 }));
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: CODE } });

    fireEvent.click(withdrawButton());

    const gone = await screen.findByRole("alert");
    expect(gone.textContent).toContain("No photographs are kept under that code.");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    // Still on the form: a mistyped code is one of the three, and the whole
    // point is that this page cannot say which.
    expect(codeBox()).toBeTruthy();
  });

  it("counts a throttled attempt down rather than offering a button", async () => {
    withdrawMock.mockRejectedValue(
      new ApiError("too many", { status: 429, retryAfterSeconds: 30 }),
    );
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: CODE } });

    fireEvent.click(withdrawButton());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Try again in 30 seconds.");
    expect(withdrawButton().disabled).toBe(true);
  });

  it("says nothing was kept when the service could not be reached", async () => {
    withdrawMock.mockRejectedValue(new ApiError("down", { status: 503, code: "provider_error" }));
    render(<WithdrawConsent />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: CODE } });

    fireEvent.click(withdrawButton());

    expect((await screen.findByRole("alert")).textContent).toContain("Nothing has been kept");
  });

  it("still asks for a code when the agreed words cannot be loaded", async () => {
    getConsentTextMock.mockRejectedValue(new Error("offline"));
    render(<WithdrawConsent />);

    // The reverse of the upload screen's rule: consenting to text nobody could
    // load is impossible, but withdrawing must never depend on reading it.
    expect(await screen.findByLabelText("Your code")).toBeTruthy();
  });
});
