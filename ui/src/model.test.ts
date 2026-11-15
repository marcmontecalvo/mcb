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
