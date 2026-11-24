// Settings fields drawn from /api/schema. The schema carries the hints that make
// them friendly (mclab.play_runs.decorate_schema): checkbox lists, a wave builder,
// help for each choice, and text that fits the kind of run being set up.
import { createContext, useContext, useEffect, useRef, useState } from "react";
import {
  type ArenaMeta,
  type Choices,
  type Slot,
  type Where,
  MAX_PLAYERS,
  addPlayer,
  fieldText,
  fitRoster,
  isBotName,
  nextBotName,
  rosterOf,
  rosterProblems,
  teamCounts,
  humanize,
  joinChoices,
  joinWaves,
  parseWaves,
  splitChoices,
  toggleChoice,
  type WaveMob,
} from "./forms";
import { RosterPicker, useRosterBook } from "./RosterPicker";
import {
  type Current,
  type Known,
  type Setup,
  choicesFor,
  lastSetup,
  lastSummary,
  sameRoster,
  teamsOnOffer,
} from "./roster";
import {
  type Json,
  type Schema,
  getPath,
  isLocked,
  nonNullSchema,
  positionFrom,
  schemaAt,
} from "./model";

export type House = { path: string; label: string };
export const HousesCtx = createContext<House[]>([]);
export const SchemaCtx = createContext<Schema>({});
export const WhereCtx = createContext<Where>({});

const words = (s: string) =>
  s.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
// Filled in from the backend the model runs on; never set by hand.
const derived = new Set([
  "model.model",
  "model.deadline_s",
  "model.planner_model",
  "model.prompt_identity",
  "model.planner_prompt_identity",
]);
const CUSTOM = "__custom__";
type Validity = (id: string, valid: boolean) => void;

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="copy"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          },
          () => setDone(false),
        );
      }}
    >
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function JsonInput({
  value,
  onChange,
  id,
  validity,
}: {
  value: Json;
  onChange: (v: Json) => void;
  id: string;
  validity: Validity;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  useEffect(() => {
    setText(JSON.stringify(value, null, 2));
    setError("");
  }, [JSON.stringify(value)]);
  useEffect(() => () => validity(id, true), [id]);
  return (
    <>
      <textarea
        id={id}
        rows={3}
        value={text}
        aria-invalid={!!error}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setError("");
            validity(id, true);
          } catch {
            setError("Enter valid JSON.");
            validity(id, false);
          }
        }}
      />
      {error && <span className="field-error">{error}</span>}
    </>
  );
}

function PositionInput({
  id,
  title,
  help,
  value,
  onChange,
  validity,
}: {
  id: string;
  title: string;
  help?: string;
  value: Json;
  onChange: (v: Json) => void;
  validity: Validity;
}) {
  const from = (v: Json) => (Array.isArray(v) ? v.map(String) : ["", "", ""]);
  const [parts, setParts] = useState(from(value));
  useEffect(() => {
    setParts(from(value));
  }, [JSON.stringify(value)]);
  useEffect(() => () => validity(id, true), [id]);
  const set = (i: number, text: string) => {
    const next = parts.map((p, j) => (j === i ? text : p));
    setParts(next);
    const position = positionFrom(next);
    validity(id, position !== undefined);
    if (position !== undefined) onChange(position);
  };
  const bad = positionFrom(parts) === undefined;
  return (
    <fieldset className="field position" aria-invalid={bad}>
      <legend>{title}</legend>
      {["X", "Y", "Z"].map((axis, i) => (
        <label key={axis} htmlFor={`${id}-${axis}`}>
          {axis}
          <input
            id={`${id}-${axis}`}
            type="number"
            step={1}
            value={parts[i]}
            onChange={(e) => set(i, e.target.value)}
          />
        </label>
      ))}
      {bad ? (
        <span className="field-error">Fill in all three whole numbers, or none.</span>
      ) : (
        help && <small>{help}</small>
      )}
    </fieldset>
  );
}

const typed = (text: string, separator: string) =>
  text
    .split(separator)
    .map((t) => t.trim())
    .filter(Boolean);

/** A joined value ("sword+bow+armor") as grouped checkboxes, plus a box for
 * anything not on the list when the field allows it. */
export function ChoicesInput({
  id,
  title,
  help,
  choices,
  value,
  onChange,
}: {
  id: string;
  title: string;
  help?: string;
  choices: Choices;
  value: Json;
  onChange: (v: Json) => void;
}) {
  const { picked, other } = splitChoices(value, choices);
  const shown = (list: string[]) =>
    list.join(choices.separator === "," ? ", " : choices.separator);
  const [otherText, setOtherText] = useState(shown(other));
  useEffect(() => {
    if (typed(otherText, choices.separator).join() !== other.join())
      setOtherText(shown(other));
  }, [JSON.stringify(value)]);
  const groups = Array.from(new Set(choices.options.map((o) => o.group || "")));
  return (
    <fieldset className="field wide choices" id={id}>
      <legend>{title}</legend>
      {help && <small>{help}</small>}
      {groups.map((group) => (
        <div className="choice-group" key={group}>
          {group && <h5>{group}</h5>}
          <div className="choice-grid">
            {choices.options
              .filter((o) => (o.group || "") === group)
              .map((o) => (
                <label className="choice" key={o.value}>
                  <input
                    type="checkbox"
                    checked={picked.includes(o.value)}
                    onChange={(e) =>
                      onChange(
                        joinChoices(
                          toggleChoice(picked, o.value, e.target.checked, choices),
                          other,
                          choices,
                        ),
                      )
                    }
                  />
                  <span>
                    <strong>{o.label}</strong>
                    {o.help && <small>{o.help}</small>}
                  </span>
                </label>
              ))}
          </div>
        </div>
      ))}
      {choices.other && (
        <label className="choice-other" htmlFor={`${id}-other`}>
          {choices.other}
          <input
            id={`${id}-other`}
            value={otherText}
            onChange={(e) => {
              setOtherText(e.target.value);
              onChange(joinChoices(picked, typed(e.target.value, choices.separator), choices));
            }}
          />
        </label>
      )}
      {!picked.length && !other.length && choices.empty && (
        <small>Nothing ticked: {humanize(choices.empty)}.</small>
      )}
    </fieldset>
  );
}

/** "zombie*3+skeleton, creeper" as a list of waves, each a list of mobs. */
export function WavesInput({
  id,
  title,
  help,
  mobs,
  value,
  onChange,
  validity,
}: {
  id: string;
  title: string;
  help?: string;
  mobs: string[];
  value: Json;
  onChange: (v: Json) => void;
  validity: Validity;
}) {
  const [waves, setWaves] = useState(() => parseWaves(value));
  useEffect(() => {
    if (joinWaves(waves) !== (value || null)) setWaves(parseWaves(value));
  }, [JSON.stringify(value)]);
  const empty = !joinWaves(waves);
  useEffect(() => {
    validity(id, !empty);
  }, [id, empty]);
  useEffect(() => () => validity(id, true), [id]);
  const commit = (next: WaveMob[][]) => {
    setWaves(next);
    onChange(joinWaves(next));
  };
  const first = mobs[0] || "zombie";
  const edit = (w: number, fn: (wave: WaveMob[]) => WaveMob[]) =>
    commit(waves.map((wave, i) => (i === w ? fn(wave) : wave)));
  return (
    <fieldset className="field wide waves" id={id}>
      <legend>{title}</legend>
      {help && <small>{help}</small>}
      {waves.map((wave, w) => (
        <div className="wave" key={w}>
          <div className="wave-head">
            <strong>Wave {w + 1}</strong>
            <button
              type="button"
              className="link"
              onClick={() => commit(waves.filter((_, i) => i !== w))}
            >
              Remove wave
            </button>
          </div>
          {wave.map((m, r) => (
            <div className="wave-row" key={r}>
              <select
                aria-label={`Wave ${w + 1} mob ${r + 1}`}
                value={m.mob}
                onChange={(e) =>
                  edit(w, (x) =>
                    x.map((y, j) => (j === r ? { ...y, mob: e.target.value } : y)),
                  )
                }
              >
                {!mobs.includes(m.mob) && <option value={m.mob}>{humanize(m.mob)}</option>}
                {mobs.map((mob) => (
                  <option key={mob} value={mob}>
                    {humanize(mob)}
                  </option>
                ))}
              </select>
              <span aria-hidden>×</span>
              <input
                aria-label={`Wave ${w + 1} how many ${humanize(m.mob)}`}
                type="number"
                min={1}
                max={50}
                value={m.count}
                onChange={(e) =>
                  edit(w, (x) =>
                    x.map((y, j) =>
                      j === r ? { ...y, count: Number(e.target.value) || 0 } : y,
                    ),
                  )
                }
              />
              <button
                type="button"
                className="link"
                aria-label={`Remove ${humanize(m.mob)} from wave ${w + 1}`}
                onClick={() => edit(w, (x) => x.filter((_, j) => j !== r))}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            className="link"
            onClick={() => edit(w, (x) => [...x, { mob: first, count: 1 }])}
          >
            + Add a mob
          </button>
        </div>
      ))}
      <button
        type="button"
        className="secondary-small"
        onClick={() => commit([...waves, [{ mob: first, count: 1 }]])}
      >
        + Add a wave
      </button>
      {empty && <span className="field-error">Add at least one mob.</span>}
    </fieldset>
  );
}

const PLAYER = /^[A-Za-z0-9_]{1,16}$/;

/** Who plays an arena match: bots (MCBot, MCBot2, ...) and people by Minecraft name,
 * each on one of the arena's teams in a team game, and whether the match makes
 * them an operator (never a bot). The roster follows the game and arena: a team
 * game puts everyone on a team.
 *
 * People are picked, not typed: "Who's here" offers who is online on the arena
 * server and the names saved from earlier matches (RosterPicker), and a wizard
 * that has not been set up starts from the last match's roster, teams and setup
 * (`adopt` writes them into the form; `current` and `known` say what is set and
 * what still exists). */
export function RosterInput({
  id,
  title,
  help,
  value,
  onChange,
  validity,
  sided,
  meta,
  bot,
  max = MAX_PLAYERS,
  adopt,
  current,
  chosen,
  known,
}: {
  id: string;
  title: string;
  help?: string;
  value: Json;
  onChange: (v: Json) => void;
  validity: Validity;
  sided: boolean;
  meta?: ArenaMeta;
  bot: string;
  max?: number;
  adopt?: (setup: Setup) => void;
  /** The roster, arena, game and waves as the form holds them (defaults included). */
  current?: Current;
  /** The same, but only what the user has changed: null where they have not. */
  chosen?: Current;
  known?: Known;
}) {
  const roster = rosterOf(value);
  const [name, setName] = useState("");
  const set = (next: Slot[]) => onChange(next as unknown as Json);
  const people = useRosterBook();
  const last = people.book?.last_match ?? null;
  const offered = known ?? { arenas: [], games: [], waves: [] };
  const nothing: Current = { roster: null, arena: null, game: null, waves: null, custom_waves: null };
  const started = useRef(false);
  useEffect(() => {
    // Once, when the saved roster arrives: a wizard the user has not touched starts
    // from the last match.
    if (started.current || !people.book) return;
    started.current = true;
    const setup = lastSetup(people.book.last_match, chosen ?? nothing, offered);
    if (setup) adopt ? adopt(setup) : set(setup.roster);
  }, [people.book]);
  useEffect(() => {
    const fitted = fitRoster(roster, sided, meta);
    if (fitted !== roster) set(fitted);
  }, [sided, JSON.stringify(meta), JSON.stringify(value)]);
  const problems = rosterProblems(roster, sided, meta, bot);
  useEffect(() => {
    validity(id, problems.length === 0);
  }, [id, problems.length]);
  useEffect(() => () => validity(id, true), [id]);
  const isBot = (p: Slot) => isBotName(p.name, bot);
  const nextBot = nextBotName(roster, bot);
  const edit = (i: number, change: Partial<Slot>) =>
    set(roster.map((p, j) => (j === i ? { ...p, ...change } : p)));
  const room = Math.min(max, (!sided && meta?.players) || max);
  const full = roster.length >= room;
  const typed = name.trim();
  const taken = roster.some((p) => p.name.toLowerCase() === typed.toLowerCase());
  const addable = PLAYER.test(typed) && !taken && !full;
  const add = () => {
    set(addPlayer(roster, typed, sided, meta));
    setName("");
  };
  const who = (p: Slot, i: number) => p.name || `Player ${i + 1}`;
  const saved = people.book?.players ?? [];
  const choices = choicesFor(people.online?.online ?? [], saved, roster, bot);
  const typedSaved = saved.some((s) => s.name.toLowerCase() === typed.toLowerCase());
  const again = last && lastSetup(last, current ?? { ...nothing, roster: value }, offered, true);
  const sameAsLast = !!last && sameRoster(roster, last.roster) && !again?.arena && !again?.game;
  return (
    <fieldset className="field wide roster" id={id}>
      <legend>{title}</legend>
      {help && <small>{help}</small>}
      {last &&
        (sameAsLast ? (
          <small className="ok">Same as last time.</small>
        ) : (
          <div className="same-as-last">
            <button
              type="button"
              className="secondary-small"
              onClick={() => {
                if (!again) return;
                if (adopt) adopt(again);
                else set(again.roster);
              }}
            >
              Same as last time
            </button>
            <small>{lastSummary(last.roster)}</small>
          </div>
        ))}
      {sided && meta && (
        <p className="team-counts">
          {teamCounts(roster, meta)
            .map((c) => `${humanize(c.team)} ${c.count}${c.room ? `/${c.room}` : ""}`)
            .join(" · ")}
        </p>
      )}
      <div className="roster-rows">
        {roster.map((p, i) => (
          <div className="roster-row" key={i}>
            {isBot(p) ? (
              <span className="who">
                <strong>{p.name}</strong>{" "}
                <small>{p.name.toLowerCase() === bot.toLowerCase() ? "the bot" : "a bot"}</small>
              </span>
            ) : (
              <input
                className="who"
                aria-label={`Player ${i + 1} name`}
                value={p.name}
                onChange={(e) => edit(i, { name: e.target.value.trim() })}
              />
            )}
            {sided && meta && (
              <select
                aria-label={`${who(p, i)} team`}
                value={p.team ?? ""}
                onChange={(e) => edit(i, { team: e.target.value || null })}
              >
                {p.team && !meta.teams.includes(p.team) && (
                  <option value={p.team}>{humanize(p.team)}</option>
                )}
                {!p.team && <option value="">Choose…</option>}
                {meta.teams.map((t) => (
                  <option key={t} value={t}>
                    {humanize(t)}
                  </option>
                ))}
              </select>
            )}
            <label className="op" title="An operator on the arena server">
              <input
                type="checkbox"
                aria-label={`${who(p, i)} is an operator`}
                checked={p.op}
                disabled={isBot(p)}
                onChange={(e) => edit(i, { op: e.target.checked })}
              />
              Op
            </label>
            <button
              type="button"
              className="link"
              aria-label={`Remove ${who(p, i)}`}
              onClick={() => set(roster.filter((_, j) => j !== i))}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      <RosterPicker
        choices={choices}
        anyone={saved.length > 0 || (people.online?.online ?? []).some((p) => !p.bot)}
        teams={teamsOnOffer(sided, meta, roster)}
        full={full}
        loading={people.loading}
        note={people.online?.note ?? null}
        problem={people.problem}
        onRefresh={people.refresh}
        onAdd={(who, extra) => set(addPlayer(roster, who, sided, meta, extra))}
        onSave={people.save}
        onForget={people.forget}
      />
      <div className="roster-add">
        <input
          aria-label="New player's Minecraft name"
          placeholder="Minecraft name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && addable) {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="secondary-small" disabled={!addable} onClick={add}>
          + Add player
        </button>
        <button
          type="button"
          className="link"
          title="Keep this name in Who's here without adding it to the match"
          disabled={!PLAYER.test(typed) || typedSaved || isBotName(typed, bot)}
          onClick={() => {
            people.save(typed);
            setName("");
          }}
        >
          Save name
        </button>
        {nextBot && !full && (
          <button
            type="button"
            className="link"
            onClick={() => set(addPlayer(roster, nextBot, sided, meta))}
          >
            + Add a bot ({nextBot})
          </button>
        )}
      </div>
      {full && <small>The match is full: this arena holds {room}.</small>}
      {problems.map((problem) => (
        <span className="field-error" key={problem}>
          {problem}
        </span>
      ))}
    </fieldset>
  );
}

/** Whether the chosen arena is built on the arena server, and how to stand where
 * you can watch it. */
function ArenaStatus({ name }: { name: string | null }) {
  const where = useContext(WhereCtx);
  const server = where.servers?.[where.arena_server || "arena"];
  if (!name) return <small>Choose an arena.</small>;
  if (!where.arenas)
    return (
      <small>
        {where.arenas_error
          ? `Couldn't ask the arena server about arenas: ${where.arenas_error}`
          : "Checking the arena server…"}
      </small>
    );
  const arena = where.arenas[name];
  if (!arena?.built)
    return (
      <small>
        Not built yet: the arena server builds it when the match starts (a minute or
        two).
      </small>
    );
  return (
    <small className="ok">
      Built. Join the arena server ({server?.address ?? "…"}): you start in the lobby
      and the match takes you in.{" "}
      {arena.teleport && (
        <>
          An operator can watch from the spectator box: <code>{arena.teleport}</code>{" "}
          <CopyButton text={arena.teleport} />
        </>
      )}
    </small>
  );
}

export function Fields({
  schema,
  value,
  overrides,
  family,
  path = [],
  change,
  validity,
  locked,
  skip = [],
}: {
  schema: Schema;
  value: unknown;
  overrides: Record<string, Json>;
  family: string;
  path?: string[];
  change: (path: string[], value: Json) => void;
  validity: Validity;
  locked: string[];
  skip?: string[];
}) {
  const houses = useContext(HousesCtx);
  const root = useContext(SchemaCtx);
  const node = nonNullSchema(schema);
  if (schema.supported_tests && !schema.supported_tests.includes(family)) return null;
  if (isLocked(path, locked) || skip.includes(path.join("."))) return null;
  if (path.length && derived.has(path.join("."))) return null;
  // Drawn inside the field that offers it (custom waves under Waves).
  if (node.format === "waves") return null;
  const read = (at: string[]) => {
    const own = getPath(overrides, at);
    return own !== undefined ? own : (getPath(value, at) ?? null);
  };
  if (node.properties) {
    return (
      <div className={path.length ? "field-group" : ""}>
        {/* The run's own settings need no heading; body, planner and model do. */}
        {path.length > 0 && path.join(".") !== "run" && (
          <h4>{schema.label || words(path.at(-1)!)}</h4>
        )}
        <div className="fields">
          {Object.entries(node.properties)
            .filter(
              ([key]) => !(path.length === 0 && ["schema_version", "run"].includes(key)),
            )
            .map(([key, child]) =>
              key === "test" ? null : (
                <Fields
                  key={key}
                  schema={child}
                  value={value}
                  overrides={overrides}
                  family={family}
                  path={[...path, key]}
                  change={change}
                  validity={validity}
                  locked={locked}
                  skip={skip}
                />
              ),
            )}
        </div>
      </div>
    );
  }
  const effective = read(path) as Json;
  const id = "field-" + path.join("-");
  const text = fieldText(node, family);
  const title = text.label || words(path.at(-1) || "Setting");
  const help = text.help || undefined;
  if (node.type === "array" && node.format === "position")
    return (
      <PositionInput
        id={id}
        title={title}
        help={help}
        value={effective}
        onChange={(v) => change(path, v)}
        validity={validity}
      />
    );
  if (node.format === "roster") {
    const parent = path.slice(0, -1);
    const game = read([...parent, "game"]);
    const arena = read([...parent, "arena"]);
    const gameNode = schemaAt(root, [...parent, "game"]);
    const arenaNode = schemaAt(root, [...parent, "arena"]);
    const sided = (gameNode ? nonNullSchema(gameNode).sided || [] : []).includes(
      String(game ?? "waves"),
    );
    const meta =
      typeof arena === "string" && arenaNode
        ? nonNullSchema(arenaNode).enum_meta?.[arena]
        : undefined;
    // What the schema offers, so last time's arena, game and waves are only
    // brought back while they still exist.
    const offered = (field: string): string[] => {
      const at = schemaAt(root, [...parent, field]);
      const choice = at ? nonNullSchema(at) : undefined;
      return (choice?.enum ?? []).filter((x): x is string => typeof x === "string");
    };
    const own = (at: string[]) => getPath(overrides, at) ?? null;
    const adopt = (setup: Setup) => {
      if (setup.arena !== undefined) change([...parent, "arena"], setup.arena);
      if (setup.game !== undefined) change([...parent, "game"], setup.game);
      if ("waves" in setup) {
        change([...parent, "waves"], setup.waves ?? null);
        change([...parent, "custom_waves"], setup.custom_waves ?? null);
      }
      change(path, setup.roster as unknown as Json);
    };
    return (
      <RosterInput
        id={id}
        title={title}
        help={help}
        value={effective}
        onChange={(v) => change(path, v)}
        validity={validity}
        sided={sided}
        meta={meta}
        bot={node.bot || "MCBot"}
        max={node.max}
        adopt={adopt}
        current={{
          roster: effective,
          arena,
          game,
          waves: read([...parent, "waves"]),
          custom_waves: read([...parent, "custom_waves"]),
        }}
        chosen={{
          roster: own(path),
          arena: own([...parent, "arena"]),
          game: own([...parent, "game"]),
          waves: own([...parent, "waves"]),
          custom_waves: own([...parent, "custom_waves"]),
        }}
        known={{
          arenas: offered("arena"),
          games: offered("game"),
          waves: offered("waves"),
        }}
      />
    );
  }
  if (node.format === "choices" && node.choices)
    return (
      <ChoicesInput
        id={id}
        title={title}
        help={help}
        choices={node.choices}
        value={effective}
        onChange={(v) => change(path, v)}
      />
    );
  if (path.join(".") === "run.build" && houses.length) {
    const known = houses.some((h) => h.path === effective);
    return (
      <div className="field">
        <label htmlFor={id}>{title}</label>
        <select id={id} value={String(effective ?? "")} onChange={(e) => change(path, e.target.value)}>
          {!known && <option value={String(effective ?? "")}>{String(effective ?? "")}</option>}
          {houses.map((h) => (
            <option key={h.path} value={h.path}>
              {h.label}
            </option>
          ))}
        </select>
        {help && <small>{help}</small>}
      </div>
    );
  }
  if ((node.type === "object" && !node.properties) || node.type === "array")
    return (
      <div className="field wide">
        <label htmlFor={id}>{title}</label>
        <JsonInput id={id} value={effective} onChange={(v) => change(path, v)} validity={validity} />
        {help && <small>{help}</small>}
      </div>
    );
  if (node.type === "boolean")
    return (
      <label className="check">
        <input
          type="checkbox"
          checked={effective === true}
          onChange={(e) => change(path, e.target.checked)}
        />
        <span>{title}</span>
        {help && <small>{help}</small>}
      </label>
    );
  if (node.enum) {
    const custom = node.custom;
    const customPath = custom ? [...path.slice(0, -1), custom.field] : [];
    const customValue = custom ? read(customPath) : null;
    const usingCustom = !!custom && effective === null && customValue !== null;
    const customNode = custom ? schemaAt(root, customPath) : undefined;
    const customText = customNode ? fieldText(nonNullSchema(customNode), family) : {};
    // The default is offered once, as "Iron (default)".
    const options = node.enum
      .filter((x) => x !== null && x !== text.placeholder)
      .map(String);
    const chosen = usingCustom ? CUSTOM : effective === null ? "" : String(effective);
    // "none" is a choice only where the field is optional (no mob waves in PvP)
    const optional = node.optional;
    const none =
      !!optional &&
      optional.values.includes(String(read([...path.slice(0, -1), optional.field]) ?? ""));
    return (
      <div className={"field" + (usingCustom ? " wide" : "")}>
        <label htmlFor={id}>{title}</label>
        <select
          id={id}
          value={chosen}
          onChange={(e) => {
            const v = e.target.value;
            if (v === CUSTOM) {
              change(path, null);
              change(customPath, customValue ?? `${customNode?.mobs?.[0] || "zombie"}*3`);
              return;
            }
            change(path, v === "" ? null : v);
            if (custom) change(customPath, null);
          }}
        >
          {(effective === null && !usingCustom) || text.placeholder || none ? (
            <option value="">
              {none
                ? optional!.label
                : text.placeholder
                  ? `${humanize(text.placeholder)} (default)`
                  : "Choose…"}
            </option>
          ) : null}
          {options.map((x) => (
            <option key={x} value={x}>
              {node.enum_labels?.[x] ?? humanize(x)}
            </option>
          ))}
          {custom && <option value={CUSTOM}>{custom.label}…</option>}
        </select>
        {!usingCustom && chosen && node.enum_help?.[chosen] ? (
          <small>{node.enum_help[chosen]}</small>
        ) : (
          help && <small>{help}</small>
        )}
        {path.join(".") === "run.arena" && <ArenaStatus name={chosen || null} />}
        {usingCustom && customNode && (
          <WavesInput
            id={"field-" + customPath.join("-")}
            title={customText.label || custom!.label}
            help={customText.help}
            mobs={nonNullSchema(customNode).mobs || []}
            value={customValue as Json}
            // "" keeps "make my own" chosen while there are no waves yet.
            onChange={(v) => change(customPath, v ?? "")}
            validity={validity}
          />
        )}
      </div>
    );
  }
  const numeric = node.type === "integer" || node.type === "number";
  return (
    <div className="field">
      <label htmlFor={id}>{title}</label>
      <input
        id={id}
        type={numeric ? "number" : "text"}
        min={node.minimum}
        max={node.maximum}
        step={node.type === "integer" ? 1 : "any"}
        placeholder={text.placeholder}
        value={effective === null ? "" : String(effective)}
        onChange={(e) =>
          change(
            path,
            e.target.value === "" ? null : numeric ? Number(e.target.value) : e.target.value,
          )
        }
      />
      {help && <small>{help}</small>}
    </div>
  );
}
