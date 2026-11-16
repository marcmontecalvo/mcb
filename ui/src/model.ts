export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Entry = {
  test_id: string;
  preset?: string;
  overrides?: Record<string, Json>;
  seeds?: number[];
  repetitions?: number;
};
export type Schema = {
  type?: string;
  title?: string;
  label?: string;
  description?: string;
  enum?: Json[];
  default?: Json;
  defaults?: Record<string, Json>;
  supported_tests?: string[];
  properties?: Record<string, Schema>;
  items?: Schema;
  anyOf?: Schema[];
  minimum?: number;
  maximum?: number;
};
export function setPath(
  value: Record<string, Json>,
  path: string[],
  next: Json,
): Record<string, Json> {
  const result = { ...value };
  const [head, ...tail] = path;
  if (!head) return result;
  result[head] = tail.length
    ? setPath(
        typeof value[head] === "object" &&
          value[head] !== null &&
          !Array.isArray(value[head])
          ? (value[head] as Record<string, Json>)
          : {},
        tail,
        next,
      )
    : next;
  return result;
}
export function getPath(value: unknown, path: string[]): Json | undefined {
  return path.reduce<unknown>(
    (v, k) =>
      v && typeof v === "object" && !Array.isArray(v)
        ? (v as Record<string, unknown>)[k]
        : undefined,
    value,
  ) as Json | undefined;
}
export function seedsFromText(text: string): number[] | undefined {
  if (!text.trim()) return undefined;
  const parts = text.split(",").map((s) => s.trim());
  if (
    parts.some((s) => !/^[-+]?\d+$/.test(s) || !Number.isSafeInteger(Number(s)))
  )
    throw new Error("Seeds must be comma-separated safe integers.");
  return parts.map(Number);
}
export function nonNullSchema(schema: Schema): Schema {
  return schema.anyOf?.find((x) => x.type !== "null") ?? schema;
}
export function mergeObjects(
  base: Record<string, Json>,
  overrides: Record<string, Json>,
): Record<string, Json> {
  const result = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    const prior = result[key];
    result[key] =
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      prior !== null &&
      typeof prior === "object" &&
      !Array.isArray(prior)
        ? mergeObjects(
            prior as Record<string, Json>,
            value as Record<string, Json>,
          )
        : value;
  }
  return result;
}
export function updateEntry(
  entries: Entry[],
  index: number,
  change: (entry: Entry) => Entry,
): Entry[] {
  return entries.map((entry, i) => (i === index ? change(entry) : entry));
}
export function supportsSeeds(family: string): boolean {
  return ["gather", "build", "natural", "courses"].includes(family);
}
export function isLocked(path: string[], locked: string[]): boolean {
  return locked.some(
    (item) => path.join(".") === item || path.join(".").startsWith(item + "."),
  );
}
export function loadedSuite(entries: Entry[]): {
  entries: Entry[];
  selected: number;
  seedText: string;
} {
  return {
    entries: structuredClone(entries),
    selected: entries.length ? 0 : -1,
    seedText: entries[0]?.seeds?.join(", ") || "",
  };
}

export const STALL_SECONDS = 20;
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}
export function progressPercent(done?: number | null, total?: number | null) {
  if (!total || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round(((done || 0) / total) * 100)));
}
/** Stalled when a progress record exists but has not been updated for >20 s. */
export function isStalled(updatedAt: number | undefined, nowSeconds: number) {
  return !!updatedAt && nowSeconds - updatedAt > STALL_SECONDS;
}
/** started_at is an epoch number or an ISO-ish string; returns epoch seconds. */
export function toEpochSeconds(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const ms = Date.parse(value);
    return Number.isNaN(ms) ? undefined : ms / 1000;
  }
  return undefined;
}
/** Schema node at a path, looking through nullable wrappers. */
export function schemaAt(schema: Schema, path: string[]): Schema | undefined {
  let node: Schema | undefined = schema;
  for (const key of path) node = node && nonNullSchema(node).properties?.[key];
  return node;
}
/** Three text boxes to a position: [x,y,z], null when all blank, undefined when incomplete or not whole numbers. */
export function positionFrom(parts: string[]): number[] | null | undefined {
  if (parts.every((p) => !p.trim())) return null;
  if (parts.some((p) => !/^[-+]?\d+$/.test(p.trim()))) return undefined;
  return parts.map(Number);
}
type Rec = Record<string, unknown>;
export const RECENT_RUNS = 5;
const stateOf = (s: Rec) => String(s.status || s.state || "unknown");
export function sessionName(s: Rec): string {
  const jobs = (s.jobs as Rec[] | undefined) || [];
  return String(s.label || s.title || jobs[0]?.label || jobs[0]?.kind || s.id || "Run");
}
/** Live activity layout: running sessions, waiting sessions, newest-first finished ones (capped), and how many were cut. */
export function splitSessions(sessions: Rec[]) {
  const active = sessions.filter((s) => ["running", "starting"].includes(stateOf(s)));
  const waiting = sessions.filter((s) => stateOf(s) === "queued");
  const when = (s: Rec) =>
    toEpochSeconds(s.finished_at) ?? toEpochSeconds(s.started_at) ?? 0;
  const done = sessions
    .filter((s) => !active.includes(s) && !waiting.includes(s))
    .sort((a, b) => when(b) - when(a));
  return {
    active,
    waiting,
    recent: done.slice(0, RECENT_RUNS),
    hidden: Math.max(0, done.length - RECENT_RUNS),
  };
}
