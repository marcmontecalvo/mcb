// Pure helpers behind the friendlier fields (checkbox lists, the wave builder,
// per-mission text), the History table and the "where to watch" card. The
// choices themselves come from /api/schema (mclab.play_runs.decorate_schema).

export type Choice = {
  value: string;
  label: string;
  group?: string | null;
  help?: string | null;
};
export type Choices = {
  options: Choice[];
  separator: string;
  empty?: string | null;
  exclusive?: string[];
  other?: string | null;
};
type Text = { label?: string; help?: string; placeholder?: string };
export type FieldNode = {
  label?: string;
  description?: string;
  placeholder?: string;
  by_test?: Record<string, Text>;
};

const tokens = (value: unknown, separator: string) =>
  typeof value === "string"
    ? value
        .split(separator)
        .map((t) => t.trim())
        .filter(Boolean)
    : [];

/** A joined value ("sword+armor") as the options it ticks and any typed extras. */
export function splitChoices(value: unknown, choices: Choices) {
  const known = new Set(choices.options.map((o) => o.value));
  const picked: string[] = [];
  const other: string[] = [];
  for (const token of tokens(value, choices.separator)) {
    if (token === choices.empty) continue;
    (known.has(token) ? picked : other).push(token);
  }
  return { picked, other };
}

/** Ticked options in the catalog's order, then the typed extras; nothing at all is
 * the field's empty value (a kit of bare hands), else null. */
export function joinChoices(
  picked: string[],
  other: string[],
  choices: Choices,
): string | null {
  const ordered = choices.options
    .map((o) => o.value)
    .filter((v) => picked.includes(v));
  const all = [...ordered, ...other.filter(Boolean)];
  return all.length ? all.join(choices.separator) : (choices.empty ?? null);
}

/** Tick or untick one option: an exclusive option ("keep what it carries")
 * stands alone. */
export function toggleChoice(
  picked: string[],
  value: string,
  on: boolean,
  choices: Choices,
): string[] {
  const exclusive = choices.exclusive || [];
  if (!on) return picked.filter((v) => v !== value);
  if (exclusive.includes(value)) return [value];
  return [...picked.filter((v) => !exclusive.includes(v) && v !== value), value];
}

export type WaveMob = { mob: string; count: number };

/** "zombie*3+skeleton, creeper": one wave per comma, mobs joined by +. */
export function parseWaves(text: unknown): WaveMob[][] {
  return tokens(text, ",").map((wave) =>
    tokens(wave, "+").map((part) => {
      const [name, count] = part.split("*");
      return {
        mob: name.replace(/^minecraft:/, ""),
        count: count ? Number(count) : 1,
      };
    }),
  );
}

export function joinWaves(waves: WaveMob[][]): string | null {
  const text = waves
    .map((wave) =>
      wave
        .filter((m) => m.mob && m.count >= 1)
        .map((m) => (m.count === 1 ? m.mob : `${m.mob}*${m.count}`))
        .join("+"),
    )
    .filter(Boolean)
    .join(",");
  return text || null;
}

/** A field's label, help and placeholder for this kind of run. */
export function fieldText(node: FieldNode, family: string): Text {
  const own = node.by_test?.[family] || {};
  return {
    label: own.label ?? node.label,
    help: own.help ?? node.description,
    placeholder: own.placeholder ?? node.placeholder,
  };
}

export function humanize(name: string): string {
  const words = name.replace(/^minecraft:/, "").replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

type Rec = Record<string, unknown>;

/** "20261124T133353634593Z" (a results folder name) as a date. */
function stampDate(stamp: unknown): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(String(stamp));
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m.map(Number);
  return new Date(Date.UTC(y, mo - 1, d, h, mi, s));
}

/** One /api/results row as the History table shows it. */
export function resultRow(row: Rec) {
  const statuses = (row.statuses as Record<string, number> | undefined) || {};
  const attempted = Number(row.attempted) || 0;
  const ticks = Number(row.median_ticks);
  return {
    path: String(row.path || ""),
    when: stampDate(row.date),
    what: [humanize(String(row.suite || "")), row.stage]
      .filter(Boolean)
      .join(": "),
    passed: row.status === "passed",
    result: attempted ? `${statuses.success || 0} of ${attempted} succeeded` : "",
    time: Number.isFinite(ticks) && ticks > 0 ? `${(ticks / 20).toFixed(1)} s` : "",
  };
}

function minutes(seconds: number): string {
  const whole = Math.round(seconds);
  const m = Math.floor(whole / 60);
  return m ? `${m} min ${whole % 60} s` : `${whole} s`;
}

/** One /api/builds row as the builds table shows it. */
export function buildRow(row: Rec) {
  const stats = (row.stats as Rec | undefined) || {};
  const file = typeof row.build === "string" ? row.build : "";
  const name = file.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") || "";
  const total = Number(stats.total_s);
  return {
    run: String(row.run || ""),
    when: stampDate(row.run),
    house: name ? humanize(name) : "Unknown house",
    result: String(stats.result || stats.state || ""),
    time: Number.isFinite(total) && total > 0 ? minutes(total) : "",
    placed: stats.placed === undefined ? "" : String(stats.placed),
  };
}

export type Where = {
  servers?: Record<
    string,
    { address?: string | null; lan_address?: string | null; label?: string }
  >;
  arena_server?: string;
  lobby?: boolean;
  bot?: string;
  arenas?: Record<
    string,
    { built?: boolean; teleport?: string | null; match?: string | null }
  >;
  arenas_error?: string;
};
// mclab.play_runs.FAMILY_SERVER: the game server each family of run drives
const SERVER: Record<string, string> = { arena: "arena", combat: "play" };

/** Where to watch a job: the server to join (from this PC and from another one
 * on the network), for an arena the command that stands you in its spectator
 * box, and the human players a match waits on to type /mcb ready. */
export function watchFor(job: Rec, where: Where) {
  const run = ((job.run_config as Rec | undefined)?.run as Rec | undefined) || {};
  const family = String(run.test || job.kind || "");
  const server = SERVER[family] || "lab";
  const arena = typeof run.arena === "string" ? run.arena : null;
  const known = arena ? where.arenas?.[arena] : undefined;
  const bot = (where.bot || "").toLowerCase();
  const humans =
    family === "arena"
      ? rosterOf(run.roster)
          .map((p) => p.name)
          .filter((name) => name && name.toLowerCase() !== bot)
      : [];
  return {
    server,
    address: where.servers?.[server]?.address ?? null,
    lanAddress: where.servers?.[server]?.lan_address ?? null,
    label: where.servers?.[server]?.label ?? server,
    arena,
    teleport: known?.teleport ?? null,
    built: known ? !!known.built : null,
    humans,
  };
}

// --- the arena roster (run.roster: mclab.run_config.RosterEntry) ---------------

export type Slot = { name: string; team: string | null; op: boolean };
/** An arena's teams and room (run.arena.enum_meta, from its plan). */
export type ArenaMeta = { teams: string[]; per_team: number | null; players: number | null };
const PLAYER = /^[A-Za-z0-9_]{1,16}$/;
export const MAX_PLAYERS = 16; // mclab.play_runs.MAX_PLAYERS

/** The well-formed players of a stored roster value. */
export function rosterOf(value: unknown): Slot[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (p): p is Record<string, unknown> =>
        !!p && typeof p === "object" && typeof (p as Rec).name === "string",
    )
    .map((p) => ({
      name: String(p.name),
      team: typeof p.team === "string" ? p.team : null,
      op: p.op === true,
    }));
}

/** A team game puts everyone on one of the arena's teams (the smallest first);
 * a game against the waves has no teams. Unchanged, the same array comes back. */
export function fitRoster(roster: Slot[], sided: boolean, meta?: ArenaMeta): Slot[] {
  if (!sided) {
    return roster.some((p) => p.team !== null)
      ? roster.map((p) => ({ ...p, team: null }))
      : roster;
  }
  const teams = meta?.teams || [];
  if (!teams.length) return roster;
  const counts = new Map(teams.map((t) => [t, 0]));
  for (const p of roster) if (p.team && counts.has(p.team)) counts.set(p.team, counts.get(p.team)! + 1);
  let changed = false;
  const fitted = roster.map((p) => {
    if (p.team && counts.has(p.team)) return p;
    const team = teams.reduce((a, b) => (counts.get(b)! < counts.get(a)! ? b : a));
    counts.set(team, counts.get(team)! + 1);
    changed = true;
    return { ...p, team };
  });
  return changed ? fitted : roster;
}

/** Add a player; `team` and `op` put them straight on a team or make them an
 * operator (a team the arena lacks, or any team in a game against the waves, is
 * dropped and the smallest team is used instead). */
export function addPlayer(
  roster: Slot[],
  name: string,
  sided: boolean,
  meta?: ArenaMeta,
  extra: { team?: string | null; op?: boolean } = {},
): Slot[] {
  const player = { name, team: extra.team ?? null, op: extra.op === true };
  return fitRoster([...roster, player], sided, meta);
}

export function teamCounts(roster: Slot[], meta: ArenaMeta) {
  return meta.teams.map((team) => ({
    team,
    count: roster.filter((p) => p.team === team).length,
    room: meta.per_team,
  }));
}

/** What stops a roster from starting (mclab.play_runs.require, in words). */
export function rosterProblems(roster: Slot[], sided: boolean, meta?: ArenaMeta): string[] {
  if (!roster.length) return ["Add at least one player."];
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const p of roster) {
    if (!PLAYER.test(p.name))
      problems.push(`"${p.name}" is not a Minecraft name (letters, digits and _, up to 16).`);
    else if (seen.has(p.name.toLowerCase())) problems.push(`${p.name} is in the match twice.`);
    seen.add(p.name.toLowerCase());
  }
  if (roster.length > MAX_PLAYERS) problems.push(`A match holds at most ${MAX_PLAYERS} players.`);
  if (!meta) return problems;
  if (!sided) {
    if (meta.players !== null && roster.length > meta.players)
      problems.push(`This arena holds ${meta.players} players; the match has ${roster.length}.`);
    return problems;
  }
  if (roster.some((p) => !p.team)) problems.push("Every player needs a team.");
  const counts = teamCounts(roster, meta);
  if (counts.filter((c) => c.count > 0).length < 2)
    problems.push("A team game needs players on at least two teams.");
  for (const c of counts)
    if (c.room !== null && c.count > c.room)
      problems.push(`Team ${c.team} has ${c.count} players; this arena holds ${c.room} a team.`);
  return problems;
}
