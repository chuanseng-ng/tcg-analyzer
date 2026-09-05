import { join } from "node:path";

import { expect, test } from "@playwright/test";

const fixture = (name: string): string => join(__dirname, "fixtures", name);

/**
 * Spec §69/M9's acceptance — "a new user can complete an end-to-end anonymous
 * analysis" — walked through the screens' own doors: nothing here types a URL
 * past the landing page, and nothing in the pipeline is faked. The API journey
 * (`services/api/tests/test_anonymous_journey.py`) pinned what the wire says
 * on these photographs; this spec asserts that the screens render it and hand
 * off to each other. Every selector is a role and an accessible name, because
 * the screens' copy is the contract their unit tests already assert.
 */
test("a new user completes an anonymous analysis without typing a URL", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Analyze a card" }).click();

  // --- Upload: nothing leaves the device until the user says so. --------------
  await expect(
    page.getByRole("heading", { name: "Photograph both sides of your card" }),
  ).toBeVisible();
  const send = page.getByRole("button", { name: "Use these photographs" });
  await expect(send).toBeDisabled();
  await page.getByLabel(/Add the front/).setInputFiles(fixture("front.jpg"));
  await page.getByLabel(/Add the back/).setInputFiles(fixture("back.jpg"));
  await expect(send).toBeEnabled();
  await send.click();

  // --- The gate. "Choose which card this is" is what runs the analysis; the
  // screen then polls for up to 20 s and goes on to `/cards` on `good` — or on
  // timeout, which is why the verdict is read off the browser's own poll
  // rather than inferred from the navigation. ---------------------------------
  const choose = page.getByRole("button", { name: "Choose which card this is" });
  await expect(choose).toBeVisible();
  const verdict = page.waitForResponse(async (response) => {
    if (!/\/analyses\/[^/]+$/.test(response.url()) || response.request().method() !== "GET") {
      return false;
    }
    try {
      const body = (await response.json()) as { status?: string };
      return body.status === "awaiting_confirmation";
    } catch {
      return false;
    }
  });
  await choose.click();
  const analysis = (await (await verdict).json()) as {
    images: { side: string; quality_status: string }[];
  };
  expect(analysis.images).toHaveLength(2);
  for (const image of analysis.images) {
    expect(image, `${image.side} should pass the gate`).toMatchObject({ quality_status: "good" });
  }

  const anyway = page.getByRole("button", { name: "Use them anyway" });
  await expect(page.getByRole("heading", { name: "Find a card" }).or(anyway)).toBeVisible();
  if (await anyway.isVisible()) {
    await anyway.click();
  }
  await expect(page).toHaveURL(/\/cards$/);

  // --- The catalog: a search that finds the seeded card. ---------------------
  await page.getByLabel("Card name").fill("Charizard");
  await page.getByRole("button", { name: "Search" }).click();
  await page
    .getByRole("link", { name: /^Charizard Base Set/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Charizard", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "This is my card" }).click();

  // --- Confirmation: "Not measured", never a percent (#91). ------------------
  await expect(page).toHaveURL(/\/identify\?card_id=/);
  await expect(page.getByText("Not measured")).toBeVisible();
  await expect(page.getByText(/\d+%/)).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm this card" }).click();
  await expect(
    page.getByRole("heading", { name: "Confirmed: this is the card you are holding." }),
  ).toBeVisible();
  // The screen pushes forward after four seconds; the link is the live way on.
  await page.getByRole("link", { name: "Set the costs" }).click();

  // --- Configuration: a blank acquisition cost is accepted as "not supplied". --
  await expect(
    page.getByRole("heading", { name: "What would grading this card cost you?" }),
  ).toBeVisible();
  await expect(page.getByLabel("Acquisition cost (optional)")).toHaveValue("");
  await page.getByRole("button", { name: "Use these figures" }).click();
  await expect(
    page.getByRole("heading", { name: "These are the figures the analysis will use." }),
  ).toBeVisible();
  await expect(page.getByText("Not supplied")).toBeVisible();
  // No click this time: the push to the results has to happen on its own.
  await expect(page).toHaveURL(/\/results$/);

  // --- Results: the admission with its two numbers, three distributions, the
  // comparison's admission and the condition block. --------------------------
  await expect(
    page.getByRole("heading", { name: "There is not enough information to say." }),
  ).toBeVisible();
  await expect(page.getByText("35%").first()).toBeVisible();
  await expect(page.getByText("50%").first()).toBeVisible();
  await expect(
    page.getByText("No market data was recorded for this analysis, so nothing above is priced."),
  ).toBeVisible();
  await expect(page.getByText("No price is recorded for this card graded.").first()).toBeVisible();

  await expect(page.getByRole("figure", { name: /grade probabilities$/ })).toHaveCount(3);

  await expect(page.getByRole("heading", { name: "Company comparison" })).toBeVisible();
  await expect(
    page.getByText(
      "No grading model declined to answer: the engine set every company aside because nothing was priced.",
    ),
  ).toBeVisible();

  await expect(page.getByRole("heading", { name: "Condition", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Front", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Back", exact: true })).toBeVisible();
  await expect(page.getByText(/^Stains: /).first()).toBeVisible();
  await expect(page.getByText("Not looked for:").first()).toBeVisible();

  // Mobile-first is a requirement: at 375px nothing scrolls sideways.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
