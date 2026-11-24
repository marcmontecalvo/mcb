import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import { Button, Card, Badge, Alert } from "@homenet/ui";
import "@homenet/ui/styles.css";
import "./style.css";
import { LiveSession, RecentRun } from "./LiveSession";
import {
  type Entry,
  type Json,
  type Schema,
  getPath,
  mergeObjects,
  nonNullSchema,
  setPath,
  seedsFromText,
  updateEntry,
  supportsSeeds,
  isLocked,
  loadedSuite,
  positionFrom,
  schemaAt,
  sessionName,
  splitSessions,
} from "./model";

type Test = {
  id: string;
  label: string;
  family: string;
  section?: string;
  advanced?: boolean;
  description: string;
  default_config: Record<string, Json>;
  locked_fields?: string[];
  basic_fields?: string[];
  server?: "lab" | "play";
};
type House = { path: string; label: string };
const HousesCtx = createContext<House[]>([]);
type Preset = {
  id: string;
  label: string;
  config: Record<string, Json>;
  supported_tests?: string[];
};
type Suite = { id: string; name: string; entries: Entry[] };
type Job = {
  id: string;
  label?: string;
  kind?: string;
  entrant?: string;
  run_config?: Json;
};
type Snapshot = {
  status?: Record<string, unknown>;
  sessions?: Record<string, unknown>[];
  results?: Record<string, unknown>[];
};
const token =
  document.querySelector<HTMLMetaElement>('meta[name="mcb-token"]')?.content ??
  "";
async function api<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: {
      "X-UI-Token": token,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error || data.detail || `Request failed (${response.status})`,
    );
  return data as T;
}
const label = (s: string) =>
  s.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const derived = new Set([
  "model.model",
  "model.deadline_s",
  "model.planner_model",
  "model.prompt_identity",
  "model.planner_prompt_identity",
]);

function JsonInput({
  value,
  onChange,
  id,
  validity,
}: {
  value: Json;
  onChange: (v: Json) => void;
  id: string;
  validity: (id: string, valid: boolean) => void;
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
  value,
  onChange,
  validity,
}: {
  id: string;
  title: string;
  value: Json;
  onChange: (v: Json) => void;
  validity: (id: string, valid: boolean) => void;
}) {
  const from = (v: Json) =>
    Array.isArray(v) ? v.map(String) : ["", "", ""];
  const [parts, setParts] = useState(from(value));
  useEffect(() => setParts(from(value)), [JSON.stringify(value)]);
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
      {bad && <span className="field-error">Enter all three whole numbers.</span>}
    </fieldset>
  );
}
function Fields({
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
  validity: (id: string, valid: boolean) => void;
  locked: string[];
  skip?: string[];
}) {
  const houses = useContext(HousesCtx);
  const node = nonNullSchema(schema);
  if (schema.supported_tests && !schema.supported_tests.includes(family))
    return null;
  if (isLocked(path, locked) || skip.includes(path.join("."))) return null;
  if (path.length && derived.has(path.join("."))) return null;
  if (node.properties) {
    return (
      <div className={path.length ? "field-group" : ""}>
        {path.length > 0 && <h4>{schema.label || label(path.at(-1)!)}</h4>}
        <div className="fields">
          {Object.entries(node.properties)
            .filter(
              ([key]) =>
                !(path.length === 0 && ["schema_version", "run"].includes(key)),
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
  const override = getPath(overrides, path);
  const effective =
    override !== undefined ? override : (getPath(value, path) ?? null);
  const id = "field-" + path.join("-");
  const title = schema.label || label(path.at(-1) || "Setting");
  if (node.type === "array" && node.format === "position")
    return (
      <PositionInput
        id={id}
        title={title}
        value={effective}
        onChange={(v) => change(path, v)}
        validity={validity}
      />
    );
  if (path.join(".") === "run.build" && houses.length) {
    const known = houses.some((h) => h.path === effective);
    return (
      <div className="field">
        <label htmlFor={id}>{title}</label>
        <select
          id={id}
          value={String(effective ?? "")}
          onChange={(e) => change(path, e.target.value)}
        >
          {!known && <option value={String(effective ?? "")}>{String(effective ?? "")}</option>}
          {houses.map((h) => (
            <option key={h.path} value={h.path}>
              {h.label}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if ((node.type === "object" && !node.properties) || node.type === "array")
    return (
      <div className="field wide">
        <label htmlFor={id}>{title}</label>
        <JsonInput
          id={id}
          value={effective}
          onChange={(v) => change(path, v)}
          validity={validity}
        />
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
        {node.description && <small>{node.description}</small>}
      </label>
    );
  if (node.enum) {
    return (
      <div className="field">
        <label htmlFor={id}>{title}</label>
        <select
          id={id}
          value={effective === null ? "" : String(effective)}
          onChange={(e) =>
            change(path, e.target.value === "" ? null : e.target.value)
          }
        >
          {effective === null && <option value="">Default / none</option>}
          {node.enum
            .filter((x) => x !== null)
            .map((x) => (
              <option key={String(x)} value={String(x)}>
                {String(x)}
              </option>
            ))}
        </select>
        {node.description && <small>{node.description}</small>}
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
        value={effective === null ? "" : String(effective)}
        onChange={(e) =>
          change(
            path,
            e.target.value === ""
              ? null
              : numeric
                ? Number(e.target.value)
                : e.target.value,
          )
        }
      />
      {node.description && <small>{node.description}</small>}
    </div>
  );
}
function App() {
  const [tests, setTests] = useState<Test[]>([]),
    [presets, setPresets] = useState<Preset[]>([]),
    [houses, setHouses] = useState<House[]>([]),
    [suites, setSuites] = useState<Suite[]>([]),
    [schema, setSchema] = useState<Schema | null>(null),
    [entries, setEntries] = useState<Entry[]>([]),
    [selected, setSelected] = useState(-1),
    [name, setName] = useState("My missions"),
    [jobs, setJobs] = useState<Job[]>([]),
    [snapshot, setSnapshot] = useState<Snapshot>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [connected, setConnected] = useState(false),
    [tab, setTab] = useState<"configure" | "activity" | "history">("configure"),
    [history, setHistory] = useState<unknown>(null),
    [seedText, setSeedText] = useState(""),
    [suiteId, setSuiteId] = useState(""),
    [showAdvanced, setShowAdvanced] = useState(false);
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const validity = (id: string, valid: boolean) =>
    setInvalid((old) => {
      if (valid && !old[id]) return old;
      const next = { ...old };
      if (valid) delete next[id];
      else next[id] = true;
      return next;
    });
  useEffect(() => {
    Promise.all([
      api<{ tests: Test[]; presets: Preset[]; houses?: House[] }>("/api/catalog"),
      api<Schema>("/api/schema"),
      api<Suite[]>("/api/suites"),
    ])
      .then(([catalog, s, stored]) => {
        setTests(catalog.tests || []);
        setPresets(catalog.presets || []);
        setHouses(catalog.houses || []);
        setSchema(s);
        setSuites(Array.isArray(stored) ? stored : []);
      })
      .catch((e) => setError(String(e.message)));
    let stream: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    const connect = () => {
      stream = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
      stream.addEventListener("snapshot", (event) => {
        try {
          setSnapshot(JSON.parse((event as MessageEvent).data));
          setConnected(true);
        } catch {
          setConnected(false);
        }
      });
      stream.onerror = () => {
        setConnected(false);
        // The browser does not retry after a non-200 reply (e.g. 503 when full).
        if (stream?.readyState === EventSource.CLOSED && !closed) {
          stream.close();
          retry = setTimeout(connect, 3000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      stream?.close();
    };
  }, []);
  const current = entries[selected],
    test = tests.find((t) => t.id === current?.test_id);
  const preset = presets.find((p) => p.id === current?.preset);
  const displayConfig = test
    ? mergeObjects(test.default_config, preset?.config || {})
    : {};
  useEffect(() => {
    setSeedText(current?.seeds?.join(", ") || "");
  }, [selected]);
  const update = (fn: (entry: Entry) => Entry) => {
    setEntries((old) => updateEntry(old, selected, fn));
    setJobs([]);
  };
  const toggle = (id: string) => {
    const index = entries.findIndex((e) => e.test_id === id);
    if (index >= 0) {
      const next = entries.filter((e) => e.test_id !== id);
      setEntries(next);
      setSelected(next.length ? 0 : -1);
    } else {
      setEntries([...entries, { test_id: id }]);
      setSelected(entries.length);
    }
    setInvalid({});
    setJobs([]);
  };
  const change = (path: string[], value: Json) =>
    update((e) => {
      let overrides = setPath(e.overrides || {}, path, value);
      if (
        path.join(".") === "model.backend" ||
        path.join(".") === "model.planner_backend"
      ) {
        const model = overrides.model;
        if (model && typeof model === "object" && !Array.isArray(model)) {
          model.model = null;
          model.deadline_s = null;
          model.planner_model = null;
          model.prompt_identity = null;
          model.planner_prompt_identity = null;
        }
      }
      return { ...e, overrides };
    });
  async function resolve() {
    if (Object.keys(invalid).length) {
      setError("Fix the settings marked in red first.");
      return null;
    }
    try {
      setError("");
      const result = await api<{ jobs: Job[] }>("/api/resolve", "POST", {
        entries,
      });
      setJobs(result.jobs);
      return result.jobs;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }
  async function save() {
    try {
      setError("");
      const result = await api<Suite>("/api/suites", "POST", { name, entries });
      setSuites(await api<Suite[]>("/api/suites"));
      setNotice(`Saved "${result.name || name}". Load it from the top of the missions list.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => window.scrollTo(0, 0), [tab]);
  async function start() {
    if (!(await resolve())) return;
    try {
      await api("/api/start", "POST", { kind: "suite", entries });
      setTab("activity");
      const servers = new Set(
        entries.map((e) => tests.find((t) => t.id === e.test_id)?.server || "lab"),
      );
      const booting = [
        servers.has("lab") && status !== "running" && "lab",
        servers.has("play") && playStatus !== "running" && "play",
      ].filter(Boolean);
      setNotice(
        booting.length
          ? `Starting the ${booting.join(" and ")} server first (a minute or two), then your missions run.`
          : "Started. Follow along here or watch the bot in Minecraft.",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function startServer(which: "lab" | "play") {
    try {
      await api("/api/start", "POST", {
        kind: "setup",
        operation: which === "lab" ? "server-start" : "play-start",
      });
      setTab("activity");
      setNotice(
        `Starting the ${which} server. The badge turns to running when it's ready.`,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function action(name: string) {
    try {
      const result = await api<{ status?: string }>(`/api/${name}`, "POST", {});
      setNotice(result.status || `${label(name)} requested.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function openHistory() {
    setTab("history");
    try {
      const [results, builds] = await Promise.all([
        api("/api/results"),
        api("/api/builds"),
      ]);
      setHistory({ results, builds });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const status = String(snapshot.status?.state || "unknown");
  const playStatus = String(snapshot.status?.play || "unknown");
  const sectionOf = (t: Test) => t.section || label(t.family);
  const groups = useMemo(
    () => Array.from(new Set(tests.map(sectionOf))),
    [tests],
  );
  const hasAdvanced = tests.some((t) => t.advanced);
  const basic = test?.basic_fields || ["repetitions"];
  return (
    <HousesCtx.Provider value={houses}>
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">M</span>
          <div>
            <strong>MCB</strong>
            <small>LAB COMPANION</small>
          </div>
        </div>
        <p className="rail-label">WORKSPACE</p>
        <nav aria-label="Workspace">
          <button
            className={tab === "configure" ? "active" : ""}
            onClick={() => setTab("configure")}
          >
            ◇ <span>Suite builder</span>
          </button>
          <button
            className={tab === "activity" ? "active" : ""}
            onClick={() => setTab("activity")}
          >
            ◉ <span>Live activity</span>
          </button>
          <button
            className={tab === "history" ? "active" : ""}
            onClick={openHistory}
          >
            ▤ <span>History & builds</span>
          </button>
        </nav>
        <div className="rail-bottom">
          <span className={"dot " + (connected ? "online" : "")} />
          {connected ? "Live connection" : "Reconnecting"}
          <a href="/legacy">Open classic dashboard ↗</a>
        </div>
      </aside>
      <main>
        <header className="top">
          <span>
            CONTROL CENTER <b>/</b>{" "}
            {tab === "configure"
              ? "SUITE BUILDER"
              : tab === "activity"
                ? "LIVE ACTIVITY"
                : "HISTORY"}
          </span>
          <div>
            <span className={"dot " + (connected ? "online" : "")} />
            {connected ? "CONNECTED" : "RECONNECTING"}
          </div>
        </header>
        <div className="content">
          <div className="heading">
            <div>
              <p className="eyebrow">MCB LAB COMPANION</p>
              <h1>
                {tab === "configure"
                  ? "Send the bot on missions"
                  : tab === "activity"
                    ? "Live activity"
                    : "History & builds"}
              </h1>
              <p>
                {tab === "configure"
                  ? "Pick what you want the bot to try, then press Start. Watch it in Minecraft or on Live activity."
                  : tab === "activity"
                    ? "Follow the current session and its jobs."
                    : "Browse previous results and build runs."}
              </p>
            </div>
            <div className="lab-state">
              <Badge tone={status === "running" ? "working" : "quiet"}>
                {status === "running" ? "lab running" : `lab ${status}`}
              </Badge>
              {status !== "running" && status !== "starting" && (
                <Button onClick={() => startServer("lab")}>Start lab</Button>
              )}
              <Badge tone={playStatus === "running" ? "working" : "quiet"}>
                {`play ${playStatus}`}
              </Badge>
              {playStatus !== "running" && (
                <Button onClick={() => startServer("play")}>Start play server</Button>
              )}
            </div>
          </div>
          {error && (
            <Alert title="Something needs attention" tone="attention">
              {error}
            </Alert>
          )}
          {notice && (
            <p className="notice" role="status">
              {notice}
            </p>
          )}
          {tab === "configure" && (
            <ol className="steps" aria-label="How it works">
              <li>
                <b>1</b>
                <span>
                  <strong>Pick missions</strong>
                  <small>Tick one or more on the left.</small>
                </span>
              </li>
              <li>
                <b>2</b>
                <span>
                  <strong>Change settings (optional)</strong>
                  <small>The defaults work fine.</small>
                </span>
              </li>
              <li>
                <b>3</b>
                <span>
                  <strong>Press Start</strong>
                  <small>Missions run one at a time.</small>
                </span>
              </li>
            </ol>
          )}
          {tab === "configure" && (
            <div className="workspace">
              <Card
                className="panel picker"
                title="Missions"
                description="Tick the missions you want. Click a name to see its settings."
              >
                {suites.length > 0 && (
                <div className="suite-load">
                  <label htmlFor="saved">Load a saved set</label>
                  <select
                    id="saved"
                    value={suiteId}
                    onChange={(e) => setSuiteId(e.target.value)}
                  >
                    <option value="">Choose a set</option>
                    {suites.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="secondary"
                    disabled={!suiteId}
                    onClick={() => {
                      const suite = suites.find((x) => x.id === suiteId);
                      if (suite) {
                        const loaded = loadedSuite(suite.entries);
                        setName(suite.name);
                        setEntries(loaded.entries);
                        setSelected(loaded.selected);
                        setSeedText(loaded.seedText);
                        setInvalid({});
                        setJobs([]);
                      }
                    }}
                  >
                    Load set
                  </Button>
                </div>
                )}
                {entries.length > 0 && (
                  <div className="test-group">
                    <h3>Your picks (run in this order)</h3>
                    {entries.map((entry, i) => (
                      <button
                        key={i}
                        className={selected === i ? "selected" : ""}
                        onClick={() => {
                          setSelected(i);
                          setInvalid({});
                        }}
                      >
                        {i + 1}.{" "}
                        {tests.find((t) => t.id === entry.test_id)?.label ||
                          entry.test_id}
                      </button>
                    ))}
                  </div>
                )}
                {groups.map((group) => {
                  const rows = tests.filter((t) => sectionOf(t) === group);
                  const hidden =
                    !showAdvanced &&
                    rows.every((t) => t.advanced) &&
                    !rows.some((t) => entries.some((e) => e.test_id === t.id));
                  if (hidden) return null;
                  return (
                  <section key={group} className="test-group">
                    <h3>{group}</h3>
                    {rows.map((t) => (
                        <div
                          className={
                            "test-row " +
                            (current?.test_id === t.id ? "selected" : "")
                          }
                          key={t.id}
                        >
                          <input
                            aria-label={`Include ${t.label}`}
                            type="checkbox"
                            checked={entries.some((e) => e.test_id === t.id)}
                            onChange={() => toggle(t.id)}
                          />
                          <button
                            onClick={() => {
                              if (!entries.some((e) => e.test_id === t.id))
                                toggle(t.id);
                              else {
                                setSelected(
                                  entries.findIndex((e) => e.test_id === t.id),
                                );
                                setInvalid({});
                              }
                            }}
                          >
                            <strong>{t.label}</strong>
                            <small>{t.description}</small>
                          </button>
                        </div>
                      ))}
                  </section>
                  );
                })}
                {hasAdvanced && (
                  <button
                    className="advanced-toggle"
                    aria-expanded={showAdvanced}
                    onClick={() => setShowAdvanced(!showAdvanced)}
                  >
                    {showAdvanced
                      ? "Hide lab tools"
                      : "Show lab tools (for developers)"}
                  </button>
                )}
              </Card>
              <div className="editor">
                <Card
                  className="panel"
                  title={test?.label || "Mission settings"}
                  description={test?.description || "Nothing picked yet."}
                >
                  {current && test && schema ? (
                    <>
                      <div className="fields">
                        {basic
                          .filter((p) => p.startsWith("run."))
                          .map((p) => (
                            <Fields
                              key={p}
                              schema={schemaAt(schema, p.split(".")) || {}}
                              path={p.split(".")}
                              value={displayConfig}
                              overrides={current.overrides || {}}
                              family={test.family}
                              change={change}
                              validity={validity}
                              locked={test.locked_fields || []}
                            />
                          ))}
                        {supportsSeeds(test.family) && basic.includes("seeds") && (
                          <div className="field">
                            <label htmlFor="seeds">Seeds (optional)</label>
                            <input
                              id="seeds"
                              placeholder="Leave blank, or e.g. 1, 2, 3"
                              value={seedText}
                              onChange={(e) => {
                                setSeedText(e.target.value);
                                try {
                                  const seeds = seedsFromText(e.target.value);
                                  update((x) => ({ ...x, seeds }));
                                  validity("seeds", true);
                                  setError("");
                                } catch (err) {
                                  validity("seeds", false);
                                  setError((err as Error).message);
                                }
                              }}
                            />
                            <small>
                              A seed picks the random layout. Same seed, same
                              layout, so runs can be compared. Each seed you
                              list is one run.
                            </small>
                          </div>
                        )}
                        {basic.includes("repetitions") && (
                        <div className="field">
                          <label htmlFor="repetitions">Times to run</label>
                          <input
                            id="repetitions"
                            type="number"
                            min="1"
                            max="3"
                            value={current.repetitions ?? ""}
                            placeholder="1"
                            onChange={(e) =>
                              update((x) => ({
                                ...x,
                                repetitions: e.target.value
                                  ? Number(e.target.value)
                                  : undefined,
                              }))
                            }
                          />
                          <small>
                            1 to 3. Running more than once shows whether it
                            succeeds every time or just got lucky.
                          </small>
                        </div>
                        )}
                      </div>
                      <details className="advanced-settings">
                        <summary>More settings</summary>
                      <div className="fields">
                        <div className="field">
                          <label htmlFor="preset">Preset</label>
                          <select
                            id="preset"
                            value={current.preset || ""}
                            onChange={(e) =>
                              update((x) => ({
                                ...x,
                                preset: e.target.value || undefined,
                              }))
                            }
                          >
                            <option value="">Built-in defaults</option>
                            {presets
                              .filter(
                                (p) =>
                                  !p.supported_tests ||
                                  p.supported_tests.includes(test.family),
                              )
                              .map((p) => (
                                <option value={p.id} key={p.id}>
                                  {p.label}
                                </option>
                              ))}
                          </select>
                        </div>
                      </div>
                      <Fields
                        schema={schema.properties?.run || {}}
                        path={["run"]}
                        value={displayConfig}
                        overrides={current.overrides || {}}
                        family={test.family}
                        change={change}
                        validity={validity}
                        locked={test.locked_fields || []}
                        skip={basic}
                      />
                      {showAdvanced && ["body", "planner", "model"].map((key) => (
                        <Fields
                          key={key}
                          schema={schema.properties?.[key] || {}}
                          path={[key]}
                          value={displayConfig}
                          overrides={current.overrides || {}}
                          family={test.family}
                          change={change}
                          validity={validity}
                          locked={test.locked_fields || []}
                        />
                      ))}
                      </details>
                    </>
                  ) : (
                    <div className="empty">
                      Tick a mission on the left. Its settings show up here,
                      but you do not have to change anything.
                    </div>
                  )}
                </Card>
                <Card
                  className="panel queue"
                  title="Ready to go"
                  description="Press Start to run your picks. Check run list shows every run first."
                >
                  <div className="queue-head">
                    <span>
                      {entries.length} MISSION{entries.length === 1 ? "" : "S"}{" "}
                      PICKED
                    </span>
                    <Button
                      variant="secondary"
                      disabled={
                        !entries.length || !!Object.keys(invalid).length
                      }
                      onClick={resolve}
                    >
                      Check run list
                    </Button>
                  </div>
                  {jobs.length ? (
                    <ol>
                      {jobs.map((job, i) => (
                        <li key={job.id || i}>
                          <span className="job-number">
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <span>
                            <strong>{job.label || job.kind || job.id}</strong>
                            <small>{job.entrant || job.kind}</small>
                          </span>
                          <details>
                            <summary>Details</summary>
                            <pre>{JSON.stringify(job.run_config, null, 2)}</pre>
                          </details>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <div className="empty">
                      {entries.length
                        ? "Press Check run list to see each run, or just press Start."
                        : "Pick at least one mission to start."}
                    </div>
                  )}
                  <div className="queue-actions">
                    <label className="suite-name">
                      Name (to save this set for later)
                      <input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                    </label>
                    <Button
                      variant="secondary"
                      disabled={
                        !entries.length ||
                        !name.trim() ||
                        !!Object.keys(invalid).length
                      }
                      onClick={save}
                    >
                      Save set
                    </Button>
                    <Button
                      disabled={
                        !entries.length || !!Object.keys(invalid).length
                      }
                      onClick={start}
                    >
                      ▶ &nbsp; Start
                    </Button>
                  </div>
                </Card>
              </div>
            </div>
          )}
          {tab === "activity" && (
            <div className="activity">
              <Card className="panel" title="Session status">
                <div className="status-line">
                  <Badge tone={status === "running" ? "working" : "quiet"}>
                    {status}
                  </Badge>
                  <span>
                    {connected
                      ? "Receiving live updates"
                      : "Waiting for connection"}
                  </span>
                </div>
                <div className="actions">
                  <Button variant="secondary" onClick={() => action("stop")}>
                    Stop after current batch
                  </Button>
                  <Button variant="danger" onClick={() => action("kill")}>
                    Cancel current run (cleanup first)
                  </Button>
                </div>
                <p>Dashboard shutdown leaves batches running.</p>
                <div className="actions">
                  <Button className="dashboard-shutdown" variant="ghost" onClick={() => action("shutdown")}>
                    Shut down dashboard
                  </Button>
                </div>
              </Card>
              {(() => {
                const { active, waiting, recent, hidden } = splitSessions(
                  snapshot.sessions || [],
                );
                const queued = [
                  ...active.flatMap((s) =>
                    ((s.jobs as Record<string, unknown>[]) || []).filter(
                      (j) => j.status === "queued",
                    ),
                  ),
                  ...waiting,
                ];
                return (
                  <>
                    <Card className="panel" title="Now running">
                      {active.length ? (
                        active.map((s, i) => (
                          <LiveSession key={String(s.id || i)} session={s} />
                        ))
                      ) : (
                        <div className="empty">
                          Nothing is running. Pick a mission and press Start!
                        </div>
                      )}
                    </Card>
                    {queued.length > 0 && (
                      <Card className="panel" title="Up next">
                        <ol className="queued-list">
                          {queued.map((j, i) => (
                            <li key={i}>{sessionName(j)}</li>
                          ))}
                        </ol>
                      </Card>
                    )}
                    {recent.length > 0 && (
                      <Card className="panel" title="Recent runs">
                        <ul className="recent-list">
                          {recent.map((s, i) => (
                            <RecentRun key={String(s.id || i)} session={s} />
                          ))}
                        </ul>
                        {hidden > 0 && (
                          <p>Older runs are on the History tab.</p>
                        )}
                      </Card>
                    )}
                  </>
                );
              })()}
            </div>
          )}
          {tab === "history" && (
            <Card className="panel" title="Results & builds">
              <p>
                Open the classic dashboard for detailed inspection and existing
                run tools.
              </p>
              <a className="text-link" href="/legacy">
                Open classic dashboard ↗
              </a>
              <pre>
                {history ? JSON.stringify(history, null, 2) : "Loading…"}
              </pre>
            </Card>
          )}
        </div>
      </main>
    </div>
    </HousesCtx.Provider>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
