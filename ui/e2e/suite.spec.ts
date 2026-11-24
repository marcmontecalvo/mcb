import { test, expect, type Page } from "@playwright/test";

const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

async function pick(page: Page, section: string, mission: string) {
  await page.locator(".pick-card", { hasText: section }).click();
  await page.locator(".pick-card", { hasText: mission }).click();
  await expect(page.getByRole("heading", { level: 2, name: mission })).toBeVisible();
}

test("choose, set up, save, check, start and follow a mixed list", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Send the bot on missions" })).toBeVisible();
  await expect(page.getByText("127.0.0.1:25565")).toBeVisible();
  await pick(page, "Big jobs", "Gather");
  await page.getByText("More settings").click();
  await page.locator("#preset").selectOption("careful");
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("11, 12");
  await page.getByLabel("Times to run", { exact: true }).fill("2");
  await page.getByRole("button", { name: "+ Add another mission" }).click();
  await pick(page, "Skills", "Crafting");
  await expect(page.getByLabel("Seeds (optional)", { exact: true })).toHaveCount(0);
  await expect(page.locator(".run-item")).toHaveCount(2);
  const resolving = page.waitForResponse((r) => r.url().endsWith("/api/resolve"));
  await page.getByRole("button", { name: "Check run list" }).click();
  const queue = await (await resolving).json();
  expect(queue.jobs).toHaveLength(5);
  expect(queue.jobs[0].run_config.planner.movement.max_gap).toBe(0);
  await page.getByText("Saved sets").click();
  await page.getByLabel("Name (to save this list for later)").fill(`${info.project.name} suite`);
  const saving = page.waitForResponse(
    (r) => r.url().endsWith("/api/suites") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save set" }).click();
  const saved = await (await saving).json();
  expect(saved.entries).toHaveLength(2);
  expect(saved.entries[0].preset).toBe("careful");
  expect(saved.entries[0].overrides ?? {}).not.toHaveProperty("schema_version");
  await page.reload();
  await page.getByText("Saved sets").click();
  await page.getByLabel("Load a saved set").selectOption(saved.id);
  await page.getByRole("button", { name: "Load set", exact: true }).click();
  await expect(page.getByLabel("Seeds (optional)", { exact: true })).toHaveValue("11, 12");
  await page.locator(".queue-actions").getByRole("button", { name: /Start$/ }).click();
  await expect(page.getByRole("heading", { name: "Live activity" })).toBeVisible();
  await expect(page.locator(".live").first()).toContainText("running");
  await expect(page.locator(".watch-steps")).toContainText("127.0.0.1:25575");
  await page.reload();
  await page.getByRole("button", { name: /Live activity/ }).click();
  await expect(page.locator(".live").first()).toContainText("running");
  await page.getByRole("button", { name: "Stop after current batch" }).click();
  await expect(page.getByText("Stop requested after current batch")).toBeVisible();
  await page.getByRole("button", { name: "Cancel current run (cleanup first)" }).click();
  await expect(page.getByText("Fixture cancellation requested")).toBeVisible();
  expect(await noSideScroll(page)).toBe(true);
  await page.screenshot({ path: info.outputPath("activity.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("an arena hunt with my own waves, a lighter kit and diamond weapons", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await pick(page, "Arenas and combat", "Bot clears an arena");
  await page.getByLabel("Arena", { exact: true }).selectOption("vault");
  await expect(page.getByText("/tp @s -325 82 -56")).toBeVisible();
  await page.getByLabel("Arena", { exact: true }).selectOption("duel");
  await expect(page.getByText("Not built on the play server yet")).toBeVisible();
  await page.getByLabel("Arena", { exact: true }).selectOption("vault");
  await page.getByLabel("Waves", { exact: true }).selectOption({ label: "Make my own waves…" });
  await page.getByLabel("Wave 1 mob 1").selectOption("skeleton");
  await page.getByRole("button", { name: "+ Add a wave" }).click();
  await page.getByLabel("Wave 2 mob 1").selectOption("creeper");
  await page.getByLabel("Wave 2 how many Creeper").fill("2");
  await page.getByText("More settings").click();
  await page.getByRole("checkbox", { name: /^Potions/ }).uncheck();
  await page.getByLabel("Weapon material").selectOption("diamond");
  await expect(page.locator(".run-item small")).toContainText("skeleton*3,creeper*2");
  const resolving = page.waitForResponse((r) => r.url().endsWith("/api/resolve"));
  await page.getByRole("button", { name: "Check run list" }).click();
  const [job] = (await (await resolving).json()).jobs;
  expect(job.run_config.run).toMatchObject({
    arena: "vault",
    waves: null,
    custom_waves: "skeleton*3,creeper*2",
    kit: "sword+axe+spear+bow+trident+shield+armor+food",
    weapon_material: "diamond",
    armor_material: null,
  });
  // Ticking "keep what it carries" clears the rest of the kit.
  await page.getByRole("checkbox", { name: /^Keep what it carries/ }).check();
  await expect(page.getByRole("checkbox", { name: /^Sword/ })).not.toBeChecked();
  // Removing every wave blocks Start instead of sending an empty list.
  await page.getByRole("button", { name: "Remove wave" }).first().click();
  await page.getByRole("button", { name: "Remove wave" }).first().click();
  await expect(page.getByText("Add at least one mob.")).toBeVisible();
  await expect(page.locator(".queue-actions").getByRole("button", { name: /Start$/ })).toBeDisabled();
  expect(await noSideScroll(page)).toBe(true);
  await page.screenshot({ path: info.outputPath("arena.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("every tab renders after switching back and forth", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  for (let round = 0; round < 2; round++) {
    await page.getByRole("button", { name: /Live activity/ }).click();
    await expect(page.getByRole("heading", { name: "Where to watch" })).toBeVisible();
    await page.getByRole("button", { name: /History & builds/ }).click();
    await expect(page.getByText("No results yet.")).toBeVisible();
    await page.getByRole("button", { name: /Run something/ }).click();
    await expect(page.getByRole("heading", { name: "What do you want to test?" })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("invalid seeds block the list; keyboard navigation still works", async ({ page }) => {
  await page.goto("/");
  await pick(page, "Big jobs", "Gather");
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("not a seed");
  await expect(page.getByRole("button", { name: "Check run list" })).toBeDisabled();
  await page.getByLabel("Seeds (optional)", { exact: true }).fill("13");
  await expect(page.getByRole("button", { name: "Check run list" })).toBeEnabled();
  await page.getByLabel("Seeds (optional)", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Times to run", { exact: true })).toBeFocused();
  expect(await noSideScroll(page)).toBe(true);
});
