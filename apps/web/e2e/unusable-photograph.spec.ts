import { join } from "node:path";

import { expect, test } from "@playwright/test";

const fixture = (name: string): string => join(__dirname, "fixtures", name);

/**
 * Spec §19: `unusable` stops the analysis. The screen has to say so, name the
 * side, and offer no way forward but a retake — the only honest thing when the
 * photograph cannot support an answer.
 */
test("an unusable photograph stays on the upload screen and names the side", async ({ page }) => {
  await page.goto("/analyze");
  await page.getByLabel(/Add the front/).setInputFiles(fixture("unusable.jpg"));
  await page.getByLabel(/Add the back/).setInputFiles(fixture("back.jpg"));
  await page.getByRole("button", { name: "Use these photographs" }).click();
  await page.getByRole("button", { name: "Choose which card this is" }).click();

  // Next's route announcer is an alert too, so the verdict is the one with the copy.
  const alert = page
    .getByRole("alert")
    .filter({ hasText: "These photographs cannot be analysed." });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("The front:");
  await expect(page.getByRole("button", { name: "Use them anyway" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/analyze$/);
});

/**
 * #271: `/results` explains the failure from the reason the row stores (#265),
 * rendered as that reason's sentence — the one that exists only through the
 * copy table's `unusable_photograph` key — with the refused side named beside
 * it. The analysis id is in this tab's `sessionStorage`, so the screen can be
 * opened directly.
 */
test("the results screen explains the refused photograph from the stored reason", async ({
  page,
}) => {
  await page.goto("/analyze");
  await page.getByLabel(/Add the front/).setInputFiles(fixture("unusable.jpg"));
  await page.getByLabel(/Add the back/).setInputFiles(fixture("back.jpg"));
  await page.getByRole("button", { name: "Use these photographs" }).click();
  await page.getByRole("button", { name: "Choose which card this is" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "These photographs cannot be analysed." }),
  ).toBeVisible();

  await page.goto("/results");

  const failure = page
    .getByRole("alert")
    .filter({ hasText: "This analysis could not be completed." });
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("The photographs could not support an analysis.");
  await expect(failure).toContainText("The front photograph could not support an analysis.");
  await expect(failure.getByRole("link", { name: "Photograph the card again" })).toHaveAttribute(
    "href",
    "/analyze",
  );
});
