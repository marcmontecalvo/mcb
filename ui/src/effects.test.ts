import { it, expect } from "vitest";

// React calls whatever an effect returns as its cleanup. An expression body such
// as `useEffect(() => window.scrollTo(0, 0))` returns its value: scrollTo returns a
// Promise in current Chrome, and calling that on the next change unmounted the
// whole app (a blank page on every tab switch). Effects take a block body, or
// return a cleanup arrow.
const sources = import.meta.glob(["./*.ts", "./*.tsx", "!./*.test.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

it("reads the app's sources", () => {
  expect(Object.keys(sources)).toContain("./main.tsx");
});

it("every effect has a block body or returns a cleanup function", () => {
  const bad: string[] = [];
  for (const [name, text] of Object.entries(sources)) {
    for (const match of text.matchAll(/useEffect\(\s*\(\)\s*=>\s*(\S)/g)) {
      const rest = text.slice(match.index! + match[0].length - 1);
      if (!rest.startsWith("{") && !/^\(\)\s*=>/.test(rest))
        bad.push(`${name}: ${rest.split("\n")[0]}`);
    }
  }
  expect(bad).toEqual([]);
});
