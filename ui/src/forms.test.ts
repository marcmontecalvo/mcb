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
  type ArenaMeta,
  type Slot,
  addPlayer,
  fitRoster,
  isBotName,
  nextBotName,
  rosterOf,
  rosterProblems,
  teamCounts,
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
      arena: { address: "127.0.0.1:25566", label: "Arena server" },
    },
    arenas: {
      vault: { built: true, teleport: "/tp @s 1 2 3", match: "running" },
      duel: { built: false, teleport: null, match: null },
    },
  };
  it("names the server a job runs on and the arena's spectator box", () => {
    const job = { kind: "arena", run_config: { run: { test: "arena", arena: "vault" } } };
    expect(watchFor(job, where)).toEqual({
      server: "arena",
      address: "127.0.0.1:25566",
      lanAddress: null,
      label: "Arena server",
      arena: "vault",
      teleport: "/tp @s 1 2 3",
      built: true,
      humans: [],
    });
  });
  it("gives the LAN address for another PC when the server has one", () => {
    const lan = {
      ...where,
      servers: {
        ...where.servers,
        play: { ...where.servers.play, lan_address: "192.168.1.20:25565" },
      },
    };
    const job = { kind: "combat", run_config: { run: { test: "combat" } } };
    expect(watchFor(job, lan).lanAddress).toBe("192.168.1.20:25565");
  });
  it("names the human players a match waits for, not the bot", () => {
    const job = (roster: unknown) => ({
      kind: "arena",
      run_config: { run: { test: "arena", arena: "vault", roster } },
    });
    const bot = { ...where, bot: "MCBot" };
    const slot = (name: string) => ({ name, team: null, op: false });
    expect(
      watchFor(job([slot("mcbot"), slot("FlamableChkn"), slot("Ann")]), bot).humans,
    ).toEqual(["FlamableChkn", "Ann"]);
    expect(watchFor(job([slot("MCBot")]), bot).humans).toEqual([]);
    expect(watchFor(job(null), bot).humans).toEqual([]);
  });
  it("counts every bot of the match (MCBot2, MCBot3...) as no human", () => {
    const job = {
      kind: "arena",
      run_config: {
        run: {
          test: "arena",
          arena: "vault",
          roster: ["MCBot", "Ann", "MCBot2", "mcbot3", "Bob"].map((name) => ({
            name,
            team: null,
            op: false,
          })),
        },
      },
    };
    expect(watchFor(job, { ...where, bot: "MCBot" }).humans).toEqual(["Ann", "Bob"]);
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

describe("the arena roster", () => {
  const duel: ArenaMeta = { teams: ["red", "blue"], per_team: 2, players: 4 };
  const slot = (name: string, team: string | null = null, op = false): Slot => ({
    name,
    team,
    op,
  });

  it("reads only well-formed players from a stored value", () => {
    expect(rosterOf(null)).toEqual([]);
    expect(rosterOf("MCBot")).toEqual([]);
    expect(rosterOf([{ name: "Ann", team: "red", op: true }, { bad: 1 }, 3])).toEqual([
      slot("Ann", "red", true),
    ]);
  });

  it("puts everyone on a team for a team game, the smallest team first", () => {
    const fitted = fitRoster([slot("MCBot"), slot("Ann"), slot("Bo", "red")], true, duel);
    expect(fitted.map((p) => p.team)).toEqual(["blue", "red", "red"]);
    // a team the arena does not have is replaced too
    expect(fitRoster([slot("Ann", "green")], true, duel)[0].team).toBe("red");
  });

  it("takes the teams off for a game against the waves", () => {
    expect(fitRoster([slot("Ann", "red", true)], false, duel)).toEqual([
      slot("Ann", null, true),
    ]);
  });

  it("leaves an already fitting roster alone (same object)", () => {
    const roster = [slot("MCBot", "red"), slot("Ann", "blue")];
    expect(fitRoster(roster, true, duel)).toBe(roster);
    const plain = [slot("Ann")];
    expect(fitRoster(plain, false, duel)).toBe(plain);
  });

  it("adds a player to the smallest team", () => {
    const roster = [slot("MCBot", "red")];
    expect(addPlayer(roster, "Ann", true, duel)).toEqual([...roster, slot("Ann", "blue")]);
    expect(addPlayer(roster, "Ann", false, duel).at(-1)).toEqual(slot("Ann"));
  });

  it("adds a person straight onto a chosen team, as an operator when asked", () => {
    const roster = [slot("MCBot", "red")];
    expect(addPlayer(roster, "Ann", true, duel, { team: "red", op: true })).toEqual([
      ...roster,
      slot("Ann", "red", true),
    ]);
    // no teams in a game against the waves, whatever the choice said
    expect(addPlayer(roster, "Ann", false, duel, { team: "red" }).at(-1)).toEqual(
      slot("Ann"),
    );
    // a team the arena does not have falls back to the smallest
    expect(addPlayer(roster, "Ann", true, duel, { team: "green" }).at(-1)).toEqual(
      slot("Ann", "blue"),
    );
  });

  it("counts each team against its room", () => {
    expect(teamCounts([slot("A", "red"), slot("B", "red")], duel)).toEqual([
      { team: "red", count: 2, room: 2 },
      { team: "blue", count: 0, room: 2 },
    ]);
  });

  it("says what stops a roster from starting", () => {
    expect(rosterProblems([], false, duel)).toEqual(["Add at least one player."]);
    expect(rosterProblems([slot("MCBot"), slot("mcbot")], false, duel)).toEqual([
      "mcbot is in the match twice.",
    ]);
    expect(rosterProblems([slot("not a name")], false, duel)[0]).toMatch(
      /not a Minecraft name/,
    );
    expect(rosterProblems([slot("A", "red"), slot("B", "red")], true, duel)).toEqual([
      "A team game needs players on at least two teams.",
    ]);
    expect(
      rosterProblems([slot("A", "red"), slot("B", "red"), slot("C", "red"), slot("D", "blue")], true, duel),
    ).toEqual(["Team red has 3 players; this arena holds 2 a team."]);
    expect(rosterProblems(["A", "B", "C", "D", "E"].map((n) => slot(n)), false, duel)).toEqual([
      "This arena holds 4 players; the match has 5.",
    ]);
    expect(rosterProblems([slot("A", "red"), slot("B", "blue")], true, duel)).toEqual([]);
    expect(rosterProblems([slot("A")], false, undefined)).toEqual([]);
  });

  it("tells a bot's name (MCBot, MCBot2, ...) from a player's", () => {
    for (const name of ["MCBot", "mcbot", "MCBot2", "MCBot16", "MCBOT99"])
      expect(isBotName(name)).toBe(true);
    for (const name of ["Ann", "MCBot1", "MCBot0", "MCBot100", "MCBotty", "xMCBot2", ""])
      expect(isBotName(name)).toBe(false);
    expect(isBotName("Pal2", "Pal")).toBe(true);
  });

  it("names the next bot: the first free of MCBot, MCBot2, MCBot3 ...", () => {
    expect(nextBotName([])).toBe("MCBot");
    expect(nextBotName([slot("Ann")])).toBe("MCBot");
    expect(nextBotName([slot("MCBot")])).toBe("MCBot2");
    expect(nextBotName([slot("MCBot"), slot("MCBot3")])).toBe("MCBot2");
    expect(nextBotName([slot("mcbot"), slot("MCBOT2"), slot("Ann")])).toBe("MCBot3");
    const full = ["MCBot", ...Array.from({ length: 15 }, (_, i) => `MCBot${i + 2}`)];
    expect(nextBotName(full.map((n) => slot(n)))).toBeNull();
  });

  it("puts the added bots on the smaller teams, beside the people", () => {
    let roster = [slot("MCBot", "red"), slot("Ann", "red")];
    roster = addPlayer(roster, nextBotName(roster)!, true, duel);
    expect(roster.at(-1)).toEqual(slot("MCBot2", "blue"));
    roster = addPlayer(roster, "Bob", true, duel);
    expect(
      rosterProblems(roster, true, duel),
    ).toEqual([]);
    expect(roster.map((p) => p.team)).toEqual(["red", "red", "blue", "blue"]);
  });

  it("accepts several bots and refuses to make one an operator", () => {
    const bots = [slot("MCBot", "red"), slot("MCBot2", "blue"), slot("MCBot3", "red")];
    expect(rosterProblems(bots, true, duel)).toEqual([]);
    expect(rosterProblems([...bots, slot("MCBot4", "blue")], true, duel)).toEqual([]);
    expect(rosterProblems([slot("MCBot2", "red", true), slot("Ann", "blue")], true, duel)).toEqual([
      "MCBot2 is a bot and cannot be an operator.",
    ]);
  });
});
