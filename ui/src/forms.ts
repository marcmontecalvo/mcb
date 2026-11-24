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
  bots?: BotBook;
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
  const humans =
    family === "arena"
      ? rosterOf(run.roster)
          .map((p) => p.name)
          .filter((name) => name && !isBotName(name, where.bots))
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
export const BOT = "MCBot"; // mclab.companion_names.DEFAULT_NAME

/** The bots the operator renamed on the arena server, id -> name (the others keep
 * their slot name MCBot, MCBot2 ...): mclab.companion_names.BotNames, served as
 * `bots` by /api/roster, /api/roster/online and /api/where. */
export type BotBook = Record<string, string>;
const SLOT = /^mcbot([2-9]|[1-9][0-9])?$/i;

const slotId = (n: number) => (n === 1 ? "default" : `mcbot${n}`);

/** The bot a name means: a renamed bot's new name, or a slot name (MCBot, then
 * MCBot2 .. MCBot99, any case: still that bot after a rename); else null. */
export function botIdFor(name: string, book: BotBook = {}): string | null {
  const lower = name.toLowerCase();
  for (const [id, renamed] of Object.entries(book)) if (renamed.toLowerCase() === lower) return id;
  const slot = SLOT.exec(name);
  return slot ? (slot[1] ? `mcbot${slot[1]}` : "default") : null;
}

/** A bot's current name: what it was renamed to, else its slot name. */
export function botNameFor(id: string, book: BotBook = {}): string {
  return book[id] || (id === "default" ? BOT : `${BOT}${id.slice("mcbot".length)}`);
}

/** Whether a name is one of the bots (mclab.companion_names.BotNames.is_bot). */
export function isBotName(name: string, book: BotBook = {}): boolean {
  return botIdFor(name, book) !== null;
}

/** The next bot to add, by its current name: the first slot (MCBot, MCBot2 ...) the
 * roster lacks under any of its names (a removed bot's is reused), or null when a
 * match's bots are all in. */
export function nextBotName(roster: Slot[], book: BotBook = {}): string | null {
  const taken = new Set(roster.map((p) => botIdFor(p.name, book)));
  for (let n = 1; n <= MAX_PLAYERS; n++)
    if (!taken.has(slotId(n))) return botNameFor(slotId(n), book);
  return null;
}

/** The roster with each bot spelled by its current name (a roster saved before a
 * rename says MCBot2 for Rex); the same array when nothing changes. */
export function currentBotNames(roster: Slot[], book: BotBook): Slot[] {
  let changed = false;
  const next = roster.map((p) => {
    const id = botIdFor(p.name, book);
    const name = id ? botNameFor(id, book) : p.name;
    if (name === p.name) return p;
    changed = true;
    return { ...p, name };
  });
  return changed ? next : roster;
}

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
export function rosterProblems(
  roster: Slot[],
  sided: boolean,
  meta?: ArenaMeta,
  book: BotBook = {},
): string[] {
  if (!roster.length) return ["Add at least one player."];
  const problems: string[] = [];
  const seen = new Set<string>();
  const bots = new Map<string, string>(); // bot id -> the name it is in under
  for (const p of roster) {
    const id = botIdFor(p.name, book);
    if (!PLAYER.test(p.name))
      problems.push(`"${p.name}" is not a Minecraft name (letters, digits and _, up to 16).`);
    else if (seen.has(p.name.toLowerCase())) problems.push(`${p.name} is in the match twice.`);
    else if (id && bots.has(id))
      problems.push(`${p.name} is ${bots.get(id)} again: a bot plays once.`);
    else if (p.op && id) problems.push(`${p.name} is a bot and cannot be an operator.`);
    seen.add(p.name.toLowerCase());
    if (id && !bots.has(id)) bots.set(id, p.name);
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
