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
