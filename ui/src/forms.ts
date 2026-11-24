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
  servers?: Record<string, { address?: string | null; label?: string }>;
  arenas?: Record<
    string,
    { built?: boolean; teleport?: string | null; match?: string | null }
  >;
  arenas_error?: string;
};
const PLAY = ["arena", "combat"]; // mclab.play_runs.FAMILIES

/** Where to watch a job: the server to join and, for an arena, the command that
 * stands you in its spectator box. */
export function watchFor(job: Rec, where: Where) {
  const run = ((job.run_config as Rec | undefined)?.run as Rec | undefined) || {};
  const family = String(run.test || job.kind || "");
  const server = PLAY.includes(family) ? "play" : "lab";
  const arena = typeof run.arena === "string" ? run.arena : null;
  const known = arena ? where.arenas?.[arena] : undefined;
  return {
    server,
    address: where.servers?.[server]?.address ?? null,
    label: where.servers?.[server]?.label ?? server,
    arena,
    teleport: known?.teleport ?? null,
    built: known ? !!known.built : null,
  };
}
