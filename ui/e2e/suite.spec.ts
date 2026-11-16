import { test, expect } from "@playwright/test";

test("configure, save, resolve, launch and recover a mixed suite", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Send the bot on missions" }),
  ).toBeVisible();
  await page.getByRole("checkbox", { name: "Include Gather" }).check();
  await page.getByText("Advanced settings (safe to ignore)").click();
  await page.locator("#preset").selectOption("careful");
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("11, 12");
  await page.getByLabel("Times to run", { exact: true }).fill("2");
  await page.getByRole("checkbox", { name: "Include Crafting" }).check();
  await expect(page.getByLabel("Seeds (optional)", { exact: true })).toHaveCount(0);
  const resolving = page.waitForResponse((response) =>
    response.url().endsWith("/api/resolve"),
  );
  await page.getByRole("button", { name: "Check run list" }).click();
  const queue = await (await resolving).json();
  expect(queue.jobs).toHaveLength(5);
  expect(queue.jobs[0].run_config.planner.movement.max_gap).toBe(0);
  await page.getByLabel("Name (to save this set for later)").fill(`${info.project.name} suite`);
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/suites") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save set" }).click();
  const saved = await (await saving).json();
  expect(saved.entries).toHaveLength(2);
  expect(saved.entries[0].preset).toBe("careful");
  expect(saved.entries[0].overrides ?? {}).not.toHaveProperty("schema_version");
  await page.reload();
  await page.getByLabel("Load a saved set").selectOption(saved.id);
  await page.getByRole("button", { name: "Load set", exact: true }).click();
  await expect(page.getByLabel("Seeds (optional)", { exact: true })).toHaveValue("11, 12");
  await page.getByRole("button", { name: "Check run list" }).click();
  await expect(page.getByRole("button", { name: /Start$/ })).toBeEnabled();
  await page.getByRole("button", { name: /Start$/ }).click();
  await expect(
    page.getByRole("heading", { name: "Live activity" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".activity summary")
      .filter({ hasText: /fixture-.*running/ })
      .first(),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /activity/i }).click();
  await expect(
    page
      .locator(".activity summary")
      .filter({ hasText: /fixture-.*running/ })
      .first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop after current batch" }).click();
  await expect(
    page.getByText("Stop requested after current batch"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Cancel current run (cleanup first)" })
    .click();
  await expect(page.getByText("Fixture cancellation requested")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("activity.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("invalid seeds block resolve; keyboard navigation remains usable", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("checkbox", { name: "Include Gather" }).check();
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("not a seed");
  await expect(
    page.getByRole("button", { name: "Check run list" }),
  ).toBeDisabled();
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("13");
  await expect(
    page.getByRole("button", { name: "Check run list" }),
  ).toBeEnabled();
  await page.getByLabel("Seeds (optional)", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Times to run", { exact: true })).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
