import { describe, it, expect } from "vitest";
import {
  type Choices,
  buildRow,
  fieldText,
  humanize,
  joinChoices,
  joinWaves,
  parseWaves,
  resultRow,
  splitChoices,
  toggleChoice,
  watchFor,
} from "./forms";

const kit: Choices = {
  options: [
    { value: "sword", label: "Sword", group: "Weapons" },
    { value: "bow", label: "Bow", group: "Weapons" },
    { value: "armor", label: "Armor", group: "Protection" },
    { value: "keep", label: "Keep what it carries", group: "Instead" },
  ],
  separator: "+",
  empty: "fist",
  exclusive: ["keep"],
  other: null,
};
const mobs: Choices = {
  options: [
    { value: "zombie", label: "Zombie" },
    { value: "skeleton", label: "Skeleton" },
  ],
  separator: ",",
  empty: null,
  exclusive: [],
  other: "Or type a group",
};

describe("checkbox fields", () => {
  it("splits a joined value into ticked options and typed extras", () => {
    expect(splitChoices("armor+sword", kit)).toEqual({
      picked: ["armor", "sword"],
      other: [],
    });
    expect(splitChoices("zombie, zombie*3,skeleton", mobs)).toEqual({
      picked: ["zombie", "skeleton"],
      other: ["zombie*3"],
    });
    expect(splitChoices("fist", kit)).toEqual({ picked: [], other: [] });
    expect(splitChoices(null, mobs)).toEqual({ picked: [], other: [] });
  });
  it("joins in the catalog's order, extras last, and an empty kit is the fist", () => {
    expect(joinChoices(["armor", "sword"], [], kit)).toBe("sword+armor");
    expect(joinChoices(["skeleton"], ["zombie*3"], mobs)).toBe("skeleton,zombie*3");
    expect(joinChoices([], [], kit)).toBe("fist");
    expect(joinChoices([], [], mobs)).toBeNull();
  });
  it("an exclusive option clears the rest, and any other clears it", () => {
    expect(toggleChoice(["sword", "bow"], "keep", true, kit)).toEqual(["keep"]);
    expect(toggleChoice(["keep"], "sword", true, kit)).toEqual(["sword"]);
    expect(toggleChoice(["sword", "bow"], "bow", false, kit)).toEqual(["sword"]);
    expect(toggleChoice(["sword"], "sword", true, kit)).toEqual(["sword"]);
  });
});

describe("the wave builder", () => {
  it("reads one wave per comma, mobs joined by + with an optional *count", () => {
    expect(parseWaves("zombie*3+skeleton, creeper")).toEqual([
      [
        { mob: "zombie", count: 3 },
        { mob: "skeleton", count: 1 },
      ],
      [{ mob: "creeper", count: 1 }],
    ]);
    expect(parseWaves(null)).toEqual([]);
    expect(parseWaves("minecraft:zombie*2")).toEqual([[{ mob: "zombie", count: 2 }]]);
  });
  it("writes waves back the same way, dropping empty rows and waves", () => {
    const waves = [
      [
        { mob: "zombie", count: 3 },
        { mob: "skeleton", count: 1 },
        { mob: "", count: 2 },
      ],
      [],
      [{ mob: "creeper", count: 0 }],
    ];
    expect(joinWaves(waves)).toBe("zombie*3+skeleton");
    expect(joinWaves([])).toBeNull();
    expect(joinWaves(parseWaves("pillager*2, pillager+vindicator"))).toBe(
      "pillager*2,pillager+vindicator",
    );
  });
});

describe("field text", () => {
  const timeout = {
    label: "Timeout",
    description: "Seconds.",
    by_test: {
      arena: { label: "Time limit per match (seconds)", help: "Lost after.", placeholder: "600" },
    },
  };
  it("uses the text meant for this kind of run", () => {
    expect(fieldText(timeout, "arena")).toEqual({
      label: "Time limit per match (seconds)",
      help: "Lost after.",
      placeholder: "600",
    });
    expect(fieldText(timeout, "battery")).toEqual({
      label: "Timeout",
      help: "Seconds.",
      placeholder: undefined,
    });
    expect(fieldText({ placeholder: "iron" }, "arena").placeholder).toBe("iron");
  });
  it("turns names into words", () => {
    expect(humanize("crash_site")).toBe("Crash site");
    expect(humanize("mixed-10")).toBe("Mixed 10");
    expect(humanize("netherite")).toBe("Netherite");
  });
});

describe("history rows", () => {
  it("reads a results summary as when, what, result and time", () => {
    const row = resultRow({
      path: "results/arena/x/summary.json",
      date: "20261124T133353634593Z",
      suite: "arena",
      stage: "crash_site · mixed-10",
      status: "passed",
      attempted: 2,
      statuses: { success: 1, lost: 1 },
      median_ticks: 896,
    });
    expect(row.when?.getUTCHours()).toBe(13);
    expect(row.when?.getUTCMinutes()).toBe(33);
    expect(row.what).toBe("Arena: crash_site · mixed-10");
    expect(row.result).toBe("1 of 2 succeeded");
    expect(row.passed).toBe(true);
    expect(row.time).toBe("44.8 s");
  });
  it("copes with a row missing its numbers", () => {
    const row = resultRow({ path: "p", date: "bad", suite: "courses", status: "failed" });
    expect(row.when).toBeNull();
    expect(row.result).toBe("");
    expect(row.time).toBe("");
    expect(row.passed).toBe(false);
  });
});

describe("build rows", () => {
  it("reads a build run as when, house, result, time and blocks placed", () => {
    const row = buildRow({
      run: "20261119T132519590425Z",
      build: "schematics/houses/small_oak_house.json",
      seed: 7,
      stats: { state: "done", result: "success", total_s: 754.2, placed: 120 },
    });
    expect(row.when?.getUTCHours()).toBe(13);
    expect(row.house).toBe("Small oak house");
    expect(row.result).toBe("success");
    expect(row.time).toBe("12 min 34 s");
    expect(row.placed).toBe("120");
  });
  it("copes with a build still being read", () => {
    const row = buildRow({ run: "x", build: null, stats: { state: "pending" } });
    expect(row.house).toBe("Unknown house");
    expect(row.result).toBe("pending");
    expect(row.time).toBe("");
    expect(row.placed).toBe("");
  });
});

describe("where to watch", () => {
  const where = {
    servers: {
      lab: { address: "127.0.0.1:25575", label: "Lab server" },
      play: { address: "127.0.0.1:25565", label: "Play server" },
    },
    arenas: {
      vault: { built: true, teleport: "/tp @s 1 2 3", match: "running" },
      duel: { built: false, teleport: null, match: null },
    },
  };
  it("names the server a job runs on and the arena's spectator box", () => {
    const job = { kind: "arena", run_config: { run: { test: "arena", arena: "vault" } } };
    expect(watchFor(job, where)).toEqual({
      server: "play",
      address: "127.0.0.1:25565",
      label: "Play server",
      arena: "vault",
      teleport: "/tp @s 1 2 3",
      built: true,
    });
  });
  it("a lab job names the lab server and no arena", () => {
    const job = { kind: "courses", run_config: { run: { test: "courses" } } };
    expect(watchFor(job, where)).toMatchObject({
      server: "lab",
      address: "127.0.0.1:25575",
      arena: null,
      teleport: null,
    });
  });
  it("an arena that is not built says so", () => {
    const job = { kind: "arena", run_config: { run: { test: "arena", arena: "duel" } } };
    expect(watchFor(job, where)).toMatchObject({ built: false, teleport: null });
    const combat = { kind: "combat", run_config: { run: { test: "combat" } } };
    expect(watchFor(combat, where)).toMatchObject({ server: "play", arena: null });
  });
});
