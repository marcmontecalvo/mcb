// Pure TypeScript types and helpers for versioned arena scenarios, side-swapping,
// single-factor diffing, and side-by-side comparison with strict failed/abandoned
// start separation (mirrors mclab.arena_scenarios).

export type ScenarioConfig = {
  arena: string;
  game: "teams" | "waves" | "race" | string;
  teams: Record<string, string[]>;
  team_mobs: Record<string, unknown>;
  team_mob_kits: Record<string, unknown>;
  waves: unknown[] | string;
  respawn_mode: "elimination" | "player" | "team" | string;
  respawn_count: number;
  kit: unknown;
  team_kits: Record<string, unknown>;
  kit_mode: string;
  kit_pool: string[];
  bot_profiles: Record<string, unknown>;
  count: number;
  timeout: number;
  [extra: string]: unknown;
};

export type ScenarioVersionSnapshot = {
  version: number;
  updated_at: string;
  factor_note: string;
  config: ScenarioConfig;
};

export type ConfigDiffEntry = {
  field: string;
  baseline: unknown;
  variant: unknown;
};

export type ScenarioRecord = {
  id: string;
  name: string;
  version: number;
  created_at: string;
  updated_at: string;
  parent_id: string | null;
  factor_note: string;
  changed_fields?: ConfigDiffEntry[];
  swapped_from: string | null;
  side_mapping?: Record<string, string>;
  config: ScenarioConfig;
  history: ScenarioVersionSnapshot[];
};

export type RunCategory = "completed" | "failed_start" | "abandoned_start";

export type RunEntryInput = {
  path?: string;
  run_id?: string;
  when?: string | null;
  outcome?: string | null;
  error?: string | null;
  winner?: string | null;
  swapped?: boolean;
  side_mapping?: Record<string, string>;
  duration_s?: number | null;
  seconds?: number | null;
  reason?: string | null;
  scenario_version?: number | null;
  damage_dealt_by_team?: Record<string, number>;
  damage_taken_by_team?: Record<string, number>;
  kills_by_team?: Record<string, number>;
};

export type RunEntry = {
  path: string;
  run_id: string;
  when: string | null;
  outcome: string;
  category: RunCategory;
  winner: string | null;
  logical_winner: string | null;
  swapped: boolean;
  duration_s: number | null;
  reason: string | null;
  scenario_version: number | null;
  damage_dealt_by_team: Record<string, number>;
  damage_taken_by_team: Record<string, number>;
  kills_by_team: Record<string, number>;
};

export type SideBreakdown = {
  completed_rounds: number;
  failed_starts: number;
  abandoned_starts: number;
  wins_by_team: Record<string, number>;
  win_rate_by_team: Record<string, number>;
  coop_wins: number;
  draws: number;
  median_duration_s: number | null;
  mean_duration_s: number | null;
  damage_dealt_by_team: Record<string, number>;
  damage_taken_by_team: Record<string, number>;
  mean_damage_dealt_by_team: Record<string, number>;
  mean_damage_taken_by_team: Record<string, number>;
  kills_by_team: Record<string, number>;
};

export type ScenarioSummaryMeta = {
  id: string;
  name: string;
  version: number;
  factor_note?: string;
  parent_id?: string | null;
  swapped_from?: string | null;
};

export type ScenarioMetrics = {
  scenario?: ScenarioSummaryMeta;
  total_runs: number;
  completed_rounds: number;
  failed_starts_count: number;
  abandoned_starts_count: number;
  wins_by_team: Record<string, number>;
  win_rate_by_team: Record<string, number>;
  coop_wins: number;
  draws: number;
  median_duration_s: number | null;
  mean_duration_s: number | null;
  damage_dealt_by_team: Record<string, number>;
  damage_taken_by_team: Record<string, number>;
  mean_damage_dealt_by_team: Record<string, number>;
  mean_damage_taken_by_team: Record<string, number>;
  kills_by_team: Record<string, number>;
  per_side_breakdown: {
    unswapped: SideBreakdown;
    swapped: SideBreakdown;
  };
  completed_runs: RunEntry[];
  failed_starts: RunEntry[];
  abandoned_starts: RunEntry[];
  runs: RunEntry[];
};

export type ComparisonDelta = {
  completed_rounds: number;
  failed_starts: number;
  abandoned_starts: number;
  median_duration_s: number | null;
  mean_duration_s: number | null;
  win_rate_by_team: Record<string, number>;
  damage_dealt_by_team: Record<string, number>;
  damage_taken_by_team: Record<string, number>;
  kills_by_team: Record<string, number>;
};

export type ComparisonResult = {
  baseline: ScenarioMetrics;
  variant: ScenarioMetrics;
  changed_fields: ConfigDiffEntry[];
  deltas: ComparisonDelta;
};

const KNOWN_TEAMS = new Set(["red", "blue", "green", "yellow"]);
const TEAM_KEYED_FIELDS = [
  "teams",
  "team_mobs",
  "team_kits",
  "team_mob_kits",
] as const;

const COMPLETED_OUTCOMES = new Set(["won", "lost", "draw"]);
const FAILED_START_OUTCOMES = new Set(["refused", "not_started", "error"]);
const ABANDONED_START_OUTCOMES = new Set([
  "stopped",
  "timeout",
  "interrupted",
  "killed",
  "abandoned",
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return (
      ka.length === kb.length &&
      ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]))
    );
  }
  return false;
}

function roundTo(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/** Recursively diff two scenario configs into sorted `{field, baseline, variant}` rows. */
export function diffScenarioConfigs(
  baseline: Record<string, unknown> | null | undefined,
  variant: Record<string, unknown> | null | undefined,
): ConfigDiffEntry[] {
  const diffs: ConfigDiffEntry[] = [];
  const walk = (prefix: string, left: unknown, right: unknown) => {
    if (deepEqual(left, right)) return;
    if (isPlainObject(left) && isPlainObject(right)) {
      const keys = Array.from(
        new Set([...Object.keys(left), ...Object.keys(right)]),
      ).sort();
      for (const key of keys) {
        const child = prefix ? `${prefix}.${key}` : key;
        if (!(key in left)) {
          diffs.push({ field: child, baseline: null, variant: deepClone(right[key]) });
        } else if (!(key in right)) {
          diffs.push({ field: child, baseline: deepClone(left[key]), variant: null });
        } else {
          walk(child, left[key], right[key]);
        }
      }
      return;
    }
    diffs.push({
      field: prefix,
      baseline: deepClone(left),
      variant: deepClone(right),
    });
  };
  walk("", baseline ?? {}, variant ?? {});
  return diffs.sort((a, b) => a.field.localeCompare(b.field));
}

/** Apply a single dot-path edit to a deep copy of `config`. */
export function applyConfigEdit(
  config: ScenarioConfig,
  dotPath: string,
  value: unknown,
): ScenarioConfig {
  const parts = dotPath
    .split(".")
    .map((p) => p.trim())
    .filter(Boolean);
  if (!parts.length) throw new Error("Edit path must be non-empty");
  const next = deepClone(config) as Record<string, unknown>;
  let cursor = next;
  for (const part of parts.slice(0, -1)) {
    const existing = cursor[part];
    if (!isPlainObject(existing)) {
      cursor[part] = {};
    }
    cursor = cursor[part] as Record<string, unknown>;
  }
  cursor[parts[parts.length - 1]] = deepClone(value);
  return next as ScenarioConfig;
}

/** Ordered active team names present in a scenario config. */
export function activeTeams(
  config: ScenarioConfig,
  arenaTeams?: string[],
): string[] {
  const found: string[] = [];
  const add = (team: string) => {
    if (team && !found.includes(team)) found.push(team);
  };
  for (const field of TEAM_KEYED_FIELDS) {
    const map = config[field];
    if (isPlainObject(map)) {
      for (const key of Object.keys(map)) add(key);
    }
  }
  if (isPlainObject(config.bot_profiles)) {
    for (const key of Object.keys(config.bot_profiles)) {
      if (found.includes(key) || KNOWN_TEAMS.has(key)) add(key);
    }
  }
  if (arenaTeams && arenaTeams.length) {
    const ordered = arenaTeams.filter((t) => found.includes(t));
    const extras = found.filter((t) => !ordered.includes(t));
    const merged = [...ordered, ...extras];
    if (merged.length >= 2) return merged;
    if (arenaTeams.length >= 2) return [...arenaTeams];
  }
  return found;
}

/** `{logicalTeam: swappedTeam}` map (2-team A<->B swap; N-team cyclic shift). */
export function sideSwapMapping(
  config: ScenarioConfig,
  arenaTeams?: string[],
): Record<string, string> {
  if (config.game === "waves") {
    throw new Error("Cannot swap sides in non-team waves mode");
  }
  const teams = activeTeams(config, arenaTeams);
  if (teams.length < 2) {
    throw new Error("Cannot swap sides with fewer than 2 active teams");
  }
  const map: Record<string, string> = {};
  for (let i = 0; i < teams.length; i += 1) {
    map[teams[i]] = teams[(i + 1) % teams.length];
  }
  return map;
}

function rotateTeamRecord<T>(
  record: Record<string, T>,
  mapping: Record<string, string>,
): Record<string, T> {
  const out: Record<string, T> = {};
  for (const [key, val] of Object.entries(record)) {
    const dest = mapping[key] ?? key;
    out[dest] = deepClone(val);
  }
  return out;
}

/** Return a copy of `config` with all team-keyed fields rotated across active teams. */
export function swapScenarioSides(
  config: ScenarioConfig,
  arenaTeams?: string[],
): ScenarioConfig {
  const mapping = sideSwapMapping(config, arenaTeams);
  const next = deepClone(config);
  for (const field of TEAM_KEYED_FIELDS) {
    const val = next[field];
    if (isPlainObject(val)) {
      (next as Record<string, unknown>)[field] = rotateTeamRecord(val, mapping);
    }
  }
  if (isPlainObject(next.bot_profiles)) {
    next.bot_profiles = rotateTeamRecord(next.bot_profiles, mapping);
  }
  return next;
}

/** Classify a run outcome strictly into completed, failed_start, or abandoned_start. */
export function classifyOutcome(
  outcome?: string | null,
  error?: string | null,
): RunCategory {
  const raw = (outcome ?? "").trim().toLowerCase();
  if (COMPLETED_OUTCOMES.has(raw)) return "completed";
  if (FAILED_START_OUTCOMES.has(raw)) return "failed_start";
  if (ABANDONED_START_OUTCOMES.has(raw)) return "abandoned_start";
  if (error) return "failed_start";
  return "abandoned_start";
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const raw =
    sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  return roundTo(raw, 2);
}

function mean(values: number[]): number | null {
  if (!values.length) return null;
  return roundTo(values.reduce((s, v) => s + v, 0) / values.length, 2);
}

function invertMapping(mapping?: Record<string, string>): Record<string, string> {
  if (!mapping) return {};
  const inv: Record<string, string> = {};
  for (const [logical, physical] of Object.entries(mapping)) {
    inv[physical] = logical;
  }
  return inv;
}

function normalizeTeamMap(
  raw: Record<string, number> | undefined,
  physToLogical: Record<string, string>,
  digits: number,
): Record<string, number> {
  const out: Record<string, number> = {};
  if (!raw) return out;
  for (const [team, val] of Object.entries(raw)) {
    if (typeof val !== "number" || !Number.isFinite(val)) continue;
    const lt = physToLogical[team] ?? team;
    out[lt] = roundTo((out[lt] ?? 0) + val, digits);
  }
  return out;
}

function aggregateSubset(entries: RunEntry[], allTeams: string[]): SideBreakdown {
  const completed = entries.filter((e) => e.category === "completed");
  const failed = entries.filter((e) => e.category === "failed_start");
  const abandoned = entries.filter((e) => e.category === "abandoned_start");

  const wins: Record<string, number> = {};
  const dealt: Record<string, number> = {};
  const taken: Record<string, number> = {};
  const kills: Record<string, number> = {};
  for (const team of allTeams) {
    wins[team] = 0;
    dealt[team] = 0;
    taken[team] = 0;
    kills[team] = 0;
  }

  let draws = 0;
  let coopWins = 0;
  const durations: number[] = [];

  for (const entry of completed) {
    if (entry.duration_s !== null) durations.push(entry.duration_s);
    const isDraw =
      entry.outcome === "draw" ||
      (entry.logical_winner === null && entry.reason === "draw");
    if (isDraw) {
      draws += 1;
    } else if (entry.logical_winner) {
      wins[entry.logical_winner] = (wins[entry.logical_winner] ?? 0) + 1;
    } else if (entry.outcome === "won") {
      coopWins += 1;
    }
    for (const [team, val] of Object.entries(entry.damage_dealt_by_team)) {
      dealt[team] = roundTo((dealt[team] ?? 0) + val, 2);
    }
    for (const [team, val] of Object.entries(entry.damage_taken_by_team)) {
      taken[team] = roundTo((taken[team] ?? 0) + val, 2);
    }
    for (const [team, val] of Object.entries(entry.kills_by_team)) {
      kills[team] = (kills[team] ?? 0) + Math.round(val);
    }
  }

  const denom = completed.length;
  const winRate: Record<string, number> = {};
  const meanDealt: Record<string, number> = {};
  const meanTaken: Record<string, number> = {};
  if (denom > 0) {
    for (const [team, count] of Object.entries(wins)) {
      winRate[team] = roundTo(count / denom, 4);
    }
    for (const [team, val] of Object.entries(dealt)) {
      meanDealt[team] = roundTo(val / denom, 2);
    }
    for (const [team, val] of Object.entries(taken)) {
      meanTaken[team] = roundTo(val / denom, 2);
    }
  }

  return {
    completed_rounds: denom,
    failed_starts: failed.length,
    abandoned_starts: abandoned.length,
    wins_by_team: wins,
    win_rate_by_team: winRate,
    coop_wins: coopWins,
    draws,
    median_duration_s: median(durations),
    mean_duration_s: mean(durations),
    damage_dealt_by_team: dealt,
    damage_taken_by_team: taken,
    mean_damage_dealt_by_team: meanDealt,
    mean_damage_taken_by_team: meanTaken,
    kills_by_team: kills,
  };
}

/** Summarize runs with strict separation of completed, failed, and abandoned starts. */
export function summarizeRuns(
  runs: RunEntryInput[],
  defaultTeams: string[] = [],
): ScenarioMetrics {
  const entries: RunEntry[] = runs.map((raw, idx) => {
    const category = classifyOutcome(raw.outcome, raw.error);
    const swapped = Boolean(raw.swapped);
    const physToLogical = swapped ? invertMapping(raw.side_mapping) : {};
    const winner = raw.winner ? String(raw.winner) : null;
    const logicalWinner = winner ? (physToLogical[winner] ?? winner) : null;
    const rawDuration = raw.duration_s ?? raw.seconds ?? null;
    const duration =
      typeof rawDuration === "number" && Number.isFinite(rawDuration)
        ? roundTo(rawDuration, 2)
        : null;
    return {
      path: String(raw.path ?? ""),
      run_id: String(raw.run_id ?? `run-${idx + 1}`),
      when: raw.when ?? null,
      outcome: raw.outcome ? String(raw.outcome) : "error",
      category,
      winner,
      logical_winner: logicalWinner,
      swapped,
      duration_s: duration,
      reason: raw.reason ?? raw.error ?? null,
      scenario_version: raw.scenario_version ?? null,
      damage_dealt_by_team: normalizeTeamMap(
        raw.damage_dealt_by_team,
        physToLogical,
        2,
      ),
      damage_taken_by_team: normalizeTeamMap(
        raw.damage_taken_by_team,
        physToLogical,
        2,
      ),
      kills_by_team: normalizeTeamMap(raw.kills_by_team, physToLogical, 0),
    };
  });

  const teams: string[] = [...defaultTeams];
  for (const e of entries) {
    const candidates = [
      ...(e.logical_winner ? [e.logical_winner] : []),
      ...Object.keys(e.damage_dealt_by_team),
      ...Object.keys(e.damage_taken_by_team),
      ...Object.keys(e.kills_by_team),
    ];
    for (const t of candidates) {
      if (t && !teams.includes(t)) teams.push(t);
    }
  }

  const overall = aggregateSubset(entries, teams);
  const unswapped = aggregateSubset(
    entries.filter((e) => !e.swapped),
    teams,
  );
  const swapped = aggregateSubset(
    entries.filter((e) => e.swapped),
    teams,
  );

  const completedRuns = entries.filter((e) => e.category === "completed");
  const failedStarts = entries.filter((e) => e.category === "failed_start");
  const abandonedStarts = entries.filter((e) => e.category === "abandoned_start");

  return {
    total_runs: entries.length,
    completed_rounds: overall.completed_rounds,
    failed_starts_count: failedStarts.length,
    abandoned_starts_count: abandonedStarts.length,
    wins_by_team: overall.wins_by_team,
    win_rate_by_team: overall.win_rate_by_team,
    coop_wins: overall.coop_wins,
    draws: overall.draws,
    median_duration_s: overall.median_duration_s,
    mean_duration_s: overall.mean_duration_s,
    damage_dealt_by_team: overall.damage_dealt_by_team,
    damage_taken_by_team: overall.damage_taken_by_team,
    mean_damage_dealt_by_team: overall.mean_damage_dealt_by_team,
    mean_damage_taken_by_team: overall.mean_damage_taken_by_team,
    kills_by_team: overall.kills_by_team,
    per_side_breakdown: { unswapped, swapped },
    completed_runs: completedRuns,
    failed_starts: failedStarts,
    abandoned_starts: abandonedStarts,
    runs: entries,
  };
}

function diffTeamRecord(
  left: Record<string, number>,
  right: Record<string, number>,
  digits: number,
): Record<string, number> {
  const keys = Array.from(
    new Set([...Object.keys(left), ...Object.keys(right)]),
  ).sort();
  const out: Record<string, number> = {};
  for (const k of keys) {
    out[k] = roundTo((right[k] ?? 0) - (left[k] ?? 0), digits);
  }
  return out;
}

/** Combine two summarized scenarios into a side-by-side ComparisonResult. */
export function compareScenarioMetrics(
  baseline: ScenarioMetrics,
  variant: ScenarioMetrics,
  baselineConfig?: ScenarioConfig,
  variantConfig?: ScenarioConfig,
): ComparisonResult {
  const changed =
    baselineConfig && variantConfig
      ? diffScenarioConfigs(baselineConfig, variantConfig)
      : [];
  const diffNum = (a: number | null, b: number | null) =>
    a === null || b === null ? null : roundTo(b - a, 2);
  return {
    baseline,
    variant,
    changed_fields: changed,
    deltas: {
      completed_rounds: variant.completed_rounds - baseline.completed_rounds,
      failed_starts: variant.failed_starts_count - baseline.failed_starts_count,
      abandoned_starts:
        variant.abandoned_starts_count - baseline.abandoned_starts_count,
      median_duration_s: diffNum(
        baseline.median_duration_s,
        variant.median_duration_s,
      ),
      mean_duration_s: diffNum(baseline.mean_duration_s, variant.mean_duration_s),
      win_rate_by_team: diffTeamRecord(
        baseline.win_rate_by_team,
        variant.win_rate_by_team,
        4,
      ),
      damage_dealt_by_team: diffTeamRecord(
        baseline.damage_dealt_by_team,
        variant.damage_dealt_by_team,
        2,
      ),
      damage_taken_by_team: diffTeamRecord(
        baseline.damage_taken_by_team,
        variant.damage_taken_by_team,
        2,
      ),
      kills_by_team: diffTeamRecord(
        baseline.kills_by_team,
        variant.kills_by_team,
        0,
      ),
    },
  };
}

// --- Display formatters ------------------------------------------------------

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const mins = Math.floor(seconds / 60);
  const rem = seconds - mins * 60;
  return `${mins}m ${rem.toFixed(1)}s`;
}

export function formatPct(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return "—";
  return `${Math.round(rate * 100)}%`;
}

export function formatDelta(
  value: number | null | undefined,
  unit = "",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}${unit}`;
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "(empty)";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
