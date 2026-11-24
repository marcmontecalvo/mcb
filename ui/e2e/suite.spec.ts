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

test("an arena match: PvP with a friend, then PvE with my own waves", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const start = page.locator(".queue-actions").getByRole("button", { name: /Start$/ });
  async function checked() {
    const resolving = page.waitForResponse((r) => r.url().endsWith("/api/resolve"));
    await page.getByRole("button", { name: "Check run list" }).click();
    const [job] = (await (await resolving).json()).jobs;
    return job.run_config.run;
  }
  await page.goto("/");
  await pick(page, "Arenas and combat", "Arena");
  await page.getByLabel("Arena", { exact: true }).selectOption("vault");
  await expect(page.getByText("/tp @s -325 82 -56")).toBeVisible();
  await page.getByLabel("Arena", { exact: true }).selectOption("duel");
  await expect(page.getByText("Not built yet: the arena server builds it")).toBeVisible();
  // PvP: everyone goes on a team, the friend added is made an operator.
  await page.getByLabel("Game", { exact: true }).selectOption({ label: "PvP" });
  await page.getByLabel("Waves", { exact: true }).selectOption({ label: "No mob waves" });
  await page.getByLabel("New player's Minecraft name").fill("Ann");
  await page.getByRole("button", { name: "+ Add player" }).click();
  await expect(page.locator(".team-counts")).toHaveText("Red 1/2 · Blue 1/2");
  await page.getByLabel("Ann is an operator").check();
  await expect(page.getByLabel("MCBot is an operator")).toBeDisabled();
  expect(await checked()).toMatchObject({
    arena: "duel",
    game: "teams",
    waves: null,
    custom_waves: null,
    roster: [
      { name: "MCBot", team: "red", op: false },
      { name: "Ann", team: "blue", op: true },
    ],
  });
  // Both on one team is no team game: Start waits until it is fixed.
  await page.getByLabel("Ann team").selectOption("red");
  await expect(page.getByText("A team game needs players on at least two teams.")).toBeVisible();
  await expect(start).toBeDisabled();
  // PvE: one side against the waves, no teams.
  await page.getByLabel("Game", { exact: true }).selectOption({ label: "PvE" });
  await expect(page.locator(".team-counts")).toHaveCount(0);
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
  expect(await checked()).toMatchObject({
    arena: "vault",
    game: "waves",
    waves: null,
    custom_waves: "skeleton*3,creeper*2",
    roster: [
      { name: "MCBot", team: null, op: false },
      { name: "Ann", team: null, op: true },
    ],
    kit: "sword+axe+spear+bow+trident+shield+armor+food",
    weapon_material: "diamond",
    armor_material: null,
  });
  // Ticking "keep what they carry" clears the rest of the kit.
  await page.getByRole("checkbox", { name: /^Keep what they carry/ }).check();
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
