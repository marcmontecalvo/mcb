import { describe, it, expect } from "vitest";
import {
  type LastMatch,
  type Saved,
  choicesFor,
  lastSetup,
  lastSummary,
  sameRoster,
  teamsOnOffer,
} from "./roster";
import type { Slot } from "./forms";

const saved = (name: string, extra: Partial<Saved> = {}): Saved => ({
  name,
  label: "",
  pinned: false,
  uses: 0,
  last_used: null,
  ...extra,
});
const slot = (name: string, team: string | null = null, op = false): Slot => ({
  name,
  team,
  op,
});
const known = {
  arenas: ["vault", "crash_site"],
  games: ["waves", "teams", "race"],
  waves: ["mixed-10", "vault-raid"],
};
const last: LastMatch = {
  at: "2026-11-24T12:00:00Z",
  arena: "crash_site",
  game: "teams",
  waves: "mixed-10",
  custom_waves: null,
  roster: [slot("Dad", "red", true), slot("Son", "blue"), slot("MCBot", "blue")],
};
const unset = { roster: null, arena: null, game: null, waves: null, custom_waves: null };

describe("choicesFor", () => {
  it("offers who is online first, then saved names that are not", () => {
    const rows = choicesFor(
      [
        { name: "Ann", bot: false },
        { name: "Zed", bot: false },
      ],
      [saved("Bo"), saved("Zed", { label: "Son" })],
      [],
      "MCBot",
    );
    expect(rows.map((r) => [r.name, r.online, r.saved])).toEqual([
      ["Ann", true, false],
      ["Zed", true, true],
      ["Bo", false, true],
    ]);
    expect(rows[1].label).toBe("Son");
  });

  it("puts pinned names first among each group and keeps the server's order", () => {
    const rows = choicesFor(
      [
        { name: "Ann", bot: false },
        { name: "Cy", bot: false },
      ],
      [saved("Dee"), saved("Eve", { pinned: true }), saved("Cy", { pinned: true })],
      [],
      "MCBot",
    );
    expect(rows.map((r) => r.name)).toEqual(["Cy", "Ann", "Eve", "Dee"]);
  });

  it("matches names in any case and shows the spelling the server has", () => {
    const [row] = choicesFor(
      [{ name: "SonOfDad", bot: false }],
      [saved("sonofdad", { label: "Son", pinned: true })],
      [],
      "MCBot",
    );
    expect(row).toMatchObject({
      name: "SonOfDad",
      label: "Son",
      pinned: true,
      online: true,
      saved: true,
    });
  });

  it("leaves out the bot and the names already in the match", () => {
    const rows = choicesFor(
      [
        { name: "MCBot", bot: true },
        { name: "Ann", bot: false },
        { name: "Bo", bot: false },
        { name: "Carpet_1", bot: true },
      ],
      [saved("mcbot"), saved("ann"), saved("Cy")],
      [slot("ANN", "red")],
      "MCBot",
    );
    expect(rows.map((r) => r.name)).toEqual(["Bo", "Cy"]);
  });

  it("is empty when nobody is online and nothing is saved", () => {
    expect(choicesFor([], [], [], "MCBot")).toEqual([]);
  });
});

describe("lastSetup", () => {
  it("is nothing without a last match or when it had no players", () => {
    expect(lastSetup(null, unset, known)).toBeNull();
    expect(lastSetup({ ...last, roster: [] }, unset, known)).toBeNull();
  });

  it("brings back the last roster, teams and setup into an untouched wizard", () => {
    expect(lastSetup(last, unset, known)).toEqual({
      roster: last.roster,
      arena: "crash_site",
      game: "teams",
      waves: "mixed-10",
      custom_waves: null,
    });
  });

  it("copies the roster so editing the form never edits the saved match", () => {
    const setup = lastSetup(last, unset, known)!;
    setup.roster[0].team = "blue";
    expect(last.roster[0].team).toBe("red");
  });

  it("leaves a roster the user has already set alone", () => {
    expect(lastSetup(last, { ...unset, roster: [slot("Ann")] }, known)).toBeNull();
    expect(lastSetup(last, { ...unset, roster: [] }, known)).toBeNull();
  });

  it("keeps the arena, game and waves the user already chose", () => {
    const setup = lastSetup(
      last,
      { ...unset, arena: "vault", game: "waves", waves: "vault-raid" },
      known,
    )!;
    expect(setup).toEqual({ roster: last.roster });
  });

  it("brings back a team game that had no mob waves", () => {
    const bare = { ...last, waves: null };
    const setup = lastSetup(bare, { ...unset, waves: "vault-raid" }, known, true)!;
    expect(setup).toMatchObject({ waves: null, custom_waves: null });
    expect(lastSetup(bare, unset, known)).toEqual({
      roster: last.roster,
      arena: "crash_site",
      game: "teams",
    });
  });

  it("leaves out an arena, game or wave preset that no longer exists", () => {
    const gone = { ...last, arena: "retired", game: "duel", waves: "old-waves" };
    expect(lastSetup(gone, unset, known)).toEqual({ roster: last.roster });
  });

  it("keeps custom waves with the game they were made for", () => {
    const custom = { ...last, waves: null, custom_waves: "pillager*2" };
    expect(lastSetup(custom, unset, known)).toEqual({
      roster: last.roster,
      arena: "crash_site",
      game: "teams",
      waves: null,
      custom_waves: "pillager*2",
    });
  });

  it("brings back waves only for the game they were played in", () => {
    const setup = lastSetup(last, { ...unset, game: "waves" }, known)!;
    expect(setup).toEqual({ roster: last.roster, arena: "crash_site" });
  });

  it("when asked outright, replaces everything with last time's setup", () => {
    const current = {
      roster: [slot("Ann")],
      arena: "vault",
      game: "waves",
      waves: "vault-raid",
      custom_waves: null,
    };
    expect(lastSetup(last, current, known, true)).toEqual({
      roster: last.roster,
      arena: "crash_site",
      game: "teams",
      waves: "mixed-10",
      custom_waves: null,
    });
  });

  it("asked outright, sets nothing that is already the same", () => {
    const current = {
      roster: [slot("Ann")],
      arena: "crash_site",
      game: "teams",
      waves: "mixed-10",
      custom_waves: null,
    };
    expect(lastSetup(last, current, known, true)).toEqual({ roster: last.roster });
  });
});

describe("sameRoster", () => {
  it("compares names (any case), teams and operators, not the order", () => {
    const a = [slot("Dad", "red", true), slot("Son", "blue")];
    expect(sameRoster(a, [slot("son", "blue"), slot("DAD", "red", true)])).toBe(true);
    expect(sameRoster(a, [slot("Dad", "red"), slot("Son", "blue")])).toBe(false);
    expect(sameRoster(a, [slot("Dad", "red", true), slot("Son", "red")])).toBe(false);
    expect(sameRoster(a, [slot("Dad", "red", true)])).toBe(false);
    expect(sameRoster([], [])).toBe(true);
  });
});

describe("lastSummary", () => {
  it("names each player and, in a team game, the team", () => {
    expect(lastSummary(last.roster)).toBe("Dad red · Son blue · MCBot blue");
    expect(lastSummary([slot("Ann"), slot("Bo")])).toBe("Ann · Bo");
  });
});

describe("teamsOnOffer", () => {
  it("lists the arena's teams in a team game and nothing otherwise", () => {
    const meta = { teams: ["red", "blue"], per_team: 4, players: null };
    expect(teamsOnOffer(true, meta)).toEqual(["red", "blue"]);
    expect(teamsOnOffer(false, meta)).toEqual([]);
    expect(teamsOnOffer(true, undefined)).toEqual([]);
  });

  it("leaves out a team that is full", () => {
    const meta = { teams: ["red", "blue"], per_team: 1, players: null };
    expect(teamsOnOffer(true, meta, [slot("Dad", "red")])).toEqual(["blue"]);
  });
});
