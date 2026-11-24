// The people the arena wizard offers (served by mclab.play_runs, kept in
// runtime/arena-roster.json by mclab.arena_roster): who is online on the arena
// server now, the names saved from earlier matches and typed by hand, and the last
// match's setup. Pure helpers; the screen is RosterPicker.tsx.
import type { ArenaMeta, Slot } from "./forms";

/** GET /api/roster: a saved name (never anything but names and counts). */
export type Saved = {
  name: string;
  label: string;
  pinned: boolean;
  uses: number;
  last_used: string | null;
};
/** The setup of the last match that started (waves, custom waves and the roster
 * as it was, teams and operators included). */
export type LastMatch = {
  at: string | null;
  arena: string | null;
  game: string | null;
  waves: string | null;
  custom_waves: string | null;
  roster: Slot[];
};
export type Book = { players: Saved[]; last_match: LastMatch | null; bot: string };
/** GET /api/roster/online: `bot` marks the companion (and any fake player). */
export type OnlinePlayer = { name: string; bot: boolean };
export type Online = { online: OnlinePlayer[]; bot: string; note: string | null };

/** One person the wizard offers: online now, saved, or both. */
export type Choice = {
  name: string;
  label: string;
  pinned: boolean;
  online: boolean;
  saved: boolean;
};

const key = (name: string) => name.toLowerCase();

/** The people to offer: those online first, then saved names who are not; pinned
 * first within each, the server's order otherwise. The bot and everyone already in
 * the match are left out. A name matches in any case; the spelling the server has
 * wins, the label and pin come from the saved entry. */
export function choicesFor(
  online: OnlinePlayer[],
  saved: Saved[],
  inMatch: Slot[],
  bot: string,
): Choice[] {
  const skip = new Set(inMatch.map((p) => key(p.name)));
  skip.add(key(bot));
  const book = new Map(saved.map((s) => [key(s.name), s]));
  const rows: Choice[] = [];
  const seen = new Set<string>();
  const push = (name: string, isOnline: boolean) => {
    const k = key(name);
    if (skip.has(k) || seen.has(k)) return;
    seen.add(k);
    const own = book.get(k);
    rows.push({
      name,
      label: own?.label ?? "",
      pinned: own?.pinned ?? false,
      online: isOnline,
      saved: !!own,
    });
  };
  for (const p of online) if (!p.bot) push(p.name, true);
  for (const s of saved) push(s.name, false);
  const pinnedFirst = (group: Choice[]) => [
    ...group.filter((r) => r.pinned),
    ...group.filter((r) => !r.pinned),
  ];
  return [
    ...pinnedFirst(rows.filter((r) => r.online)),
    ...pinnedFirst(rows.filter((r) => !r.online)),
  ];
}

/** What the wizard already holds, as the form stores it (null: not set). */
export type Current = {
  roster: unknown;
  arena: unknown;
  game: unknown;
  waves: unknown;
  custom_waves: unknown;
};
/** What the schema offers now: an arena, game or wave preset that has since been
 * removed is not brought back. */
export type Known = { arenas: string[]; games: string[]; waves: string[] };
/** The fields last time's setup changes; a key that is absent stays as it is. */
export type Setup = {
  roster: Slot[];
  arena?: string;
  game?: string;
  waves?: string | null;
  custom_waves?: string | null;
};

/** The setup that makes this match "the same as last time": the roster with its
 * teams and operators, and, where the wizard has not been set yet (or `force`), the
 * arena, game and waves it was played with. Null when there is nothing to bring back
 * or the roster is already set by the user. */
export function lastSetup(
  last: LastMatch | null,
  current: Current,
  known: Known,
  force = false,
): Setup | null {
  if (!last || !last.roster.length) return null;
  if (!force && current.roster !== null && current.roster !== undefined) return null;
  const setup: Setup = { roster: last.roster.map((p) => ({ ...p })) };
  const open = (value: unknown) => force || value === null || value === undefined;
  if (
    last.arena &&
    known.arenas.includes(last.arena) &&
    open(current.arena) &&
    last.arena !== current.arena
  )
    setup.arena = last.arena;
  let game = current.game;
  if (last.game && known.games.includes(last.game) && open(current.game)) {
    if (last.game !== current.game) setup.game = last.game;
    game = last.game;
  }
  const wavesKnown = last.waves === null || known.waves.includes(last.waves);
  const wavesOpen =
    force ||
    ((current.waves === null || current.waves === undefined) &&
      (current.custom_waves === null || current.custom_waves === undefined));
  const same = (a: unknown, b: string | null) => (a ?? null) === b;
  if (
    wavesKnown &&
    wavesOpen &&
    game === last.game &&
    !(same(current.waves, last.waves) && same(current.custom_waves, last.custom_waves))
  ) {
    setup.waves = last.waves;
    setup.custom_waves = last.custom_waves;
  }
  return setup;
}

/** Whether two rosters are the same people on the same teams with the same
 * operators (names in any case, any order). */
export function sameRoster(a: Slot[], b: Slot[]): boolean {
  if (a.length !== b.length) return false;
  const line = (p: Slot) => `${key(p.name)}|${p.team ?? ""}|${p.op ? 1 : 0}`;
  const left = a.map(line).sort();
  const right = b.map(line).sort();
  return left.every((x, i) => x === right[i]);
}

/** "Dad red · Son blue": who played last time and on which team. */
export function lastSummary(roster: Slot[]): string {
  return roster
    .map((p) => (p.team ? `${p.name} ${p.team}` : p.name))
    .join(" · ");
}

/** The teams a person can be added to straight away: the arena's teams in a team
 * game, less any that is full; none in a game against the waves. */
export function teamsOnOffer(
  sided: boolean,
  meta: ArenaMeta | undefined,
  roster: Slot[] = [],
): string[] {
  if (!sided || !meta) return [];
  return meta.teams.filter(
    (team) =>
      meta.per_team === null ||
      roster.filter((p) => p.team === team).length < meta.per_team,
  );
}
