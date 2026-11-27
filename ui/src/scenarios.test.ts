import { describe, expect, it } from "vitest";
import {
  applyConfigEdit,
  classifyOutcome,
  compareScenarioMetrics,
  diffScenarioConfigs,
  formatDelta,
  formatDuration,
  formatPct,
  sideSwapMapping,
  summarizeRuns,
  swapScenarioSides,
  type RunEntryInput,
  type ScenarioConfig,
} from "./scenarios";

function baseConfig(overrides: Partial<ScenarioConfig> = {}): ScenarioConfig {
  return {
    arena: "crossroads",
    game: "teams",
    teams: { red: ["MCBot"], blue: ["MCBot2"] },
    team_mobs: {
      red: { "minecraft:skeleton": 2 },
      blue: { "minecraft:zombie": 3 },
    },
    team_mob_kits: {
      red: { "minecraft:skeleton": "archer" },
      blue: { "minecraft:zombie": "fighter" },
    },
    waves: [],
    respawn_mode: "elimination",
    respawn_count: 0,
    kit: "fighter",
    team_kits: { red: "fighter", blue: "tank" },
    kit_mode: "per_team",
    kit_pool: ["fighter", "archer", "tank"],
    bot_profiles: { red: "aggressive", blue: "defensive" },
    count: 3,
    timeout: 300,
    ...overrides,
  };
}

describe("diffScenarioConfigs & applyConfigEdit", () => {
  it("finds single-factor and nested dot-path differences cleanly", () => {
    const a = baseConfig();
    const b = applyConfigEdit(
      applyConfigEdit(a, "team_kits.red", "archer"),
      "respawn_count",
      2,
    );
    expect(a.team_kits.red).toBe("fighter");
    expect(b.team_kits.red).toBe("archer");
    expect(b.team_kits.blue).toBe("tank");

    const diffs = diffScenarioConfigs(a, b);
    expect(diffs).toEqual([
      { field: "respawn_count", baseline: 0, variant: 2 },
      { field: "team_kits.red", baseline: "fighter", variant: "archer" },
    ]);
  });

  it("returns empty diff for identical configs", () => {
    const a = baseConfig();
    expect(diffScenarioConfigs(a, baseConfig())).toEqual([]);
  });
});

describe("swapScenarioSides & sideSwapMapping", () => {
  it("swaps all team-keyed fields for 2 teams while preserving global rules", () => {
    const cfg = baseConfig();
    const mapping = sideSwapMapping(cfg);
    expect(mapping).toEqual({ red: "blue", blue: "red" });

    const swapped = swapScenarioSides(cfg);
    expect(swapped.teams).toEqual({ red: ["MCBot2"], blue: ["MCBot"] });
    expect(swapped.team_mobs).toEqual({
      red: { "minecraft:zombie": 3 },
      blue: { "minecraft:skeleton": 2 },
    });
    expect(swapped.team_kits).toEqual({ red: "tank", blue: "fighter" });
    expect(swapped.bot_profiles).toEqual({ red: "defensive", blue: "aggressive" });
    expect(swapped.arena).toBe("crossroads");
    expect(swapped.respawn_mode).toBe("elimination");
    expect(swapped.kit).toBe("fighter");
  });

  it("cyclically shifts 4 teams and keeps bot-name keyed profiles intact", () => {
    const cfg = baseConfig({
      teams: { red: ["A"], blue: ["B"], green: ["C"], yellow: ["D"] },
      team_kits: { red: "k1", blue: "k2", green: "k3", yellow: "k4" },
      bot_profiles: { A: "aggro", B: "cautious" },
    });
    const swapped = swapScenarioSides(cfg, ["red", "blue", "green", "yellow"]);
    expect(swapped.teams).toEqual({
      blue: ["A"],
      green: ["B"],
      yellow: ["C"],
      red: ["D"],
    });
    expect(swapped.team_kits).toEqual({
      blue: "k1",
      green: "k2",
      yellow: "k3",
      red: "k4",
    });
    expect(swapped.bot_profiles).toEqual({ A: "aggro", B: "cautious" });
  });

  it("throws when fewer than 2 teams are active or in waves mode", () => {
    const pve = baseConfig({
      game: "waves",
      teams: {},
      team_mobs: {},
      team_mob_kits: {},
      team_kits: {},
      bot_profiles: {},
    });
    expect(() => swapScenarioSides(pve)).toThrow(/waves mode/i);

    const oneTeam = baseConfig({
      game: "teams",
      teams: { red: ["MCBot"] },
      team_mobs: {},
      team_mob_kits: {},
      team_kits: {},
      bot_profiles: {},
    });
    expect(() => swapScenarioSides(oneTeam)).toThrow(/2 active teams/i);
  });
});

describe("classifyOutcome, summarizeRuns & compareScenarioMetrics", () => {
  it("classifies completed, failed starts, and abandoned starts strictly", () => {
    expect(classifyOutcome("won")).toBe("completed");
    expect(classifyOutcome("lost")).toBe("completed");
    expect(classifyOutcome("draw")).toBe("completed");
    expect(classifyOutcome("refused")).toBe("failed_start");
    expect(classifyOutcome("not_started")).toBe("failed_start");
    expect(classifyOutcome("error")).toBe("failed_start");
    expect(classifyOutcome("stopped")).toBe("abandoned_start");
    expect(classifyOutcome("timeout")).toBe("abandoned_start");
    expect(classifyOutcome("interrupted")).toBe("abandoned_start");
    expect(classifyOutcome("killed")).toBe("abandoned_start");
    expect(classifyOutcome("abandoned")).toBe("abandoned_start");
    expect(classifyOutcome(undefined, "server offline")).toBe("failed_start");
  });

  it("normalizes swapped winners/damage and excludes failed/abandoned starts from win rates and duration", () => {
    const baselineRuns: RunEntryInput[] = [
      {
        path: "results/companion/arena/r1.json",
        run_id: "r1",
        when: "2026-11-27T10:00:00Z",
        outcome: "won",
        winner: "red",
        swapped: false,
        duration_s: 40,
        damage_dealt_by_team: { red: 50, blue: 20 },
        damage_taken_by_team: { red: 20, blue: 50 },
        kills_by_team: { red: 2, blue: 0 },
      },
      {
        path: "results/companion/arena/r2.json",
        run_id: "r2",
        when: "2026-11-27T10:05:00Z",
        outcome: "won",
        winner: "blue",
        swapped: true,
        side_mapping: { red: "blue", blue: "red" },
        duration_s: 60,
        damage_dealt_by_team: { blue: 44, red: 16 },
        damage_taken_by_team: { blue: 16, red: 44 },
        kills_by_team: { blue: 1, red: 0 },
      },
      {
        path: "results/companion/arena/r3.json",
        run_id: "r3",
        when: "2026-11-27T10:10:00Z",
        outcome: "refused",
        reason: "bot refused",
        swapped: false,
        duration_s: 0.1,
      },
      {
        path: "results/companion/arena/r4.json",
        run_id: "r4",
        when: "2026-11-27T10:15:00Z",
        outcome: "timeout",
        reason: "time limit",
        swapped: false,
        duration_s: 300,
      },
    ];

    const summary = summarizeRuns(baselineRuns);
    expect(summary.completed_rounds).toBe(2);
    expect(summary.failed_starts_count).toBe(1);
    expect(summary.abandoned_starts_count).toBe(1);
    expect(summary.wins_by_team).toEqual({ red: 2, blue: 0 });
    expect(summary.win_rate_by_team).toEqual({ red: 1, blue: 0 });
    expect(summary.median_duration_s).toBe(50);
    expect(summary.damage_dealt_by_team).toEqual({ red: 94, blue: 36 });
    expect(summary.damage_taken_by_team).toEqual({ red: 36, blue: 94 });
    expect(summary.failed_starts[0].reason).toBe("bot refused");
    expect(summary.abandoned_starts[0].reason).toBe("time limit");
    expect(summary.completed_runs[1].logical_winner).toBe("red");

    const variantSummary = summarizeRuns([
      {
        path: "results/companion/arena/v1.json",
        run_id: "v1",
        when: "2026-11-27T11:00:00Z",
        outcome: "won",
        winner: "blue",
        swapped: false,
        duration_s: 30,
        damage_dealt_by_team: { red: 15, blue: 60 },
        damage_taken_by_team: { red: 60, blue: 15 },
        kills_by_team: { red: 0, blue: 2 },
      },
    ]);

    const cmp = compareScenarioMetrics(
      summary,
      variantSummary,
      baseConfig({ kit: "fighter" }),
      baseConfig({ kit: "archer" }),
    );
    expect(cmp.changed_fields).toEqual([
      { field: "kit", baseline: "fighter", variant: "archer" },
    ]);
    expect(cmp.deltas.median_duration_s).toBe(-20);
    expect(cmp.deltas.win_rate_by_team.blue).toBe(1);
    expect(cmp.deltas.win_rate_by_team.red).toBe(-1);
  });

  it("formats durations, percentages, and deltas cleanly", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(45.2)).toBe("45.2 s");
    expect(formatDuration(125)).toBe("2m 5.0s");
    expect(formatPct(0.5)).toBe("50%");
    expect(formatPct(null)).toBe("—");
    expect(formatDelta(12.5, " s")).toBe("+12.5 s");
    expect(formatDelta(-3, " s")).toBe("-3.0 s");
    expect(formatDelta(0, " s")).toBe("0.0 s");
    expect(formatDelta(null)).toBe("—");
  });
});
