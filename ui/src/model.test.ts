import { describe, it, expect } from "vitest";
import {
  getPath,
  mergeObjects,
  setPath,
  seedsFromText,
  updateEntry,
  supportsSeeds,
  isLocked,
  loadedSuite,
  sessionName,
} from "./model";
describe("suite inputs", () => {
  it("changes a nested override without mutating its source", () => {
    const source = { run: { seed: 1, rotation: 0 } };
    const updated = setPath(source, ["run", "seed"], 2);
    expect(getPath(updated, ["run", "seed"])).toBe(2);
    expect(source.run.seed).toBe(1);
    expect(getPath(updated, ["run", "rotation"])).toBe(0);
  });
  it("parses and rejects seed lists", () => {
    expect(seedsFromText("1, -2")).toEqual([1, -2]);
    expect(seedsFromText("")).toBeUndefined();
    expect(() => seedsFromText("1, no")).toThrow();
    expect(() => seedsFromText("9007199254740992")).toThrow();
  });
});
it("displays preset values with explicit null and inherited nested defaults", () => {
  const defaults = { run: { seed: 1337, gather: { carry_logs: 16 } } };
  const effective = mergeObjects(defaults, { run: { seed: null } });
  expect(effective).toEqual({
    run: { seed: null, gather: { carry_logs: 16 } },
  });
  expect(defaults.run.seed).toBe(1337);
});
it("edits duplicate tests independently", () => {
  const entries = [
    { test_id: "gather", seeds: [1] },
    { test_id: "gather", seeds: [2] },
  ];
  expect(updateEntry(entries, 1, (e) => ({ ...e, seeds: [3] }))).toEqual([
    { test_id: "gather", seeds: [1] },
    { test_id: "gather", seeds: [3] },
  ]);
  expect(entries[1].seeds).toEqual([2]);
});
it("reloads saved entries and seed text even when the first test is unchanged", () => {
  const saved = [{ test_id: "gather", seeds: [4, 5] }];
  const loaded = loadedSuite(saved);
  expect(loaded.seedText).toBe("4, 5");
  loaded.entries[0].seeds?.push(6);
  expect(loadedSuite(saved).seedText).toBe("4, 5");
});
it("limits seeds and locks to supported fields", () => {
  expect(supportsSeeds("natural")).toBe(true);
  expect(supportsSeeds("models")).toBe(false);
  expect(supportsSeeds("companion")).toBe(false);
  expect(supportsSeeds("battery")).toBe(false);
  expect(isLocked(["run", "check"], ["run.check"])).toBe(true);
  expect(isLocked(["run", "seed"], ["run.check"])).toBe(false);
  expect(isLocked(["run", "gather", "carry_logs"], ["run.gather"])).toBe(true);
});
import {
  formatElapsed,
  progressPercent,
  isStalled,
  toEpochSeconds,
} from "./model";
describe("live view formatting", () => {
  it("formats elapsed time", () => {
    expect(formatElapsed(65)).toBe("1:05");
    expect(formatElapsed(3725)).toBe("1:02:05");
    expect(formatElapsed(-3)).toBe("0:00");
  });
  it("computes progress percent", () => {
    expect(progressPercent(3, 10)).toBe(30);
    expect(progressPercent(5, 0)).toBeNull();
    expect(progressPercent(null, 4)).toBe(0);
    expect(progressPercent(9, 4)).toBe(100);
  });
  it("detects stalls after 20 s", () => {
    expect(isStalled(100, 120)).toBe(false);
    expect(isStalled(100, 121)).toBe(true);
    expect(isStalled(undefined, 500)).toBe(false);
  });
  it("parses start times", () => {
    expect(toEpochSeconds(12)).toBe(12);
    expect(toEpochSeconds("1970-01-01T00:00:10Z")).toBe(10);
    expect(toEpochSeconds("nope")).toBeUndefined();
  });
});

import { positionFrom, schemaAt } from "./model";
describe("position and schema helpers", () => {
  it("parses three boxes", () => {
    expect(positionFrom(["-24", "64", " 56"])).toEqual([-24, 64, 56]);
    expect(positionFrom(["", "", ""])).toBeNull();
    expect(positionFrom(["1", "", "3"])).toBeUndefined();
    expect(positionFrom(["1", "2.5", "3"])).toBeUndefined();
  });
  it("finds nested schema through nullable wrappers", () => {
    const leaf = { type: "integer" };
    const s = { properties: { run: { properties: { seed: { anyOf: [leaf, { type: "null" }] } } } } };
    expect(schemaAt(s, ["run", "seed"])?.anyOf?.[0]).toBe(leaf);
    expect(schemaAt(s, ["run", "nope"])).toBeUndefined();
  });
  it("keeps an optional field's enum, format and label on its non-null branch", () => {
    const arena = {
      label: "Arena",
      enum: ["crash_site", "vault"],
      anyOf: [{ type: "string" }, { type: "null" }],
    };
    expect(nonNullSchema(arena)).toEqual({
      label: "Arena",
      enum: ["crash_site", "vault"],
      type: "string",
    });
    const corner = {
      format: "position",
      anyOf: [{ type: "array", items: { type: "integer" } }, { type: "null" }],
    };
    expect(nonNullSchema(corner).format).toBe("position");
    expect(nonNullSchema(corner).type).toBe("array");
    expect(nonNullSchema({ type: "boolean" })).toEqual({ type: "boolean" });
  });
});
import { nonNullSchema } from "./model";
import { splitSessions } from "./model";
describe("splitSessions", () => {
  it("separates active, queued and caps recent newest-first", () => {
    const old = Array.from({ length: 7 }, (_, i) => ({ id: `d${i}`, status: "finished", finished_at: i }));
    const r = splitSessions([
      ...old,
      { id: "a", status: "running" },
      { id: "q", status: "queued" },
    ]);
    expect(r.active.map((s) => s.id)).toEqual(["a"]);
    expect(r.waiting.map((s) => s.id)).toEqual(["q"]);
    expect(r.recent.map((s) => s.id)).toEqual(["d6", "d5", "d4", "d3", "d2"]);
    expect(r.hidden).toBe(2);
  });
});
describe("session names", () => {
  it("names a session after what was asked for, not the server it started first", () => {
    const setup = { kind: "setup", label: "Start play server" };
    const hunt = { kind: "arena", label: "Bot clears an arena" };
    const combat = { kind: "combat", label: "Combat trials" };
    expect(sessionName({ jobs: [setup, hunt] })).toBe("Bot clears an arena");
    expect(sessionName({ jobs: [setup, hunt, combat] })).toBe(
      "Bot clears an arena + 1 more",
    );
    expect(sessionName({ jobs: [setup] })).toBe("Start play server");
    expect(sessionName({ label: "Mine", jobs: [setup, hunt] })).toBe("Mine");
    expect(sessionName({ id: "x" })).toBe("x");
  });
});
