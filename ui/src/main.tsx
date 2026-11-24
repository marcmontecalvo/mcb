import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, Card, Badge, Alert } from "@homenet/ui";
import "@homenet/ui/styles.css";
import "./style.css";
import { api, token } from "./api";
import { Fields, type House, HousesCtx, SchemaCtx, WhereCtx } from "./fields";
import { type Where, fieldText, humanize } from "./forms";
import { History } from "./History";
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
  loadedSuite,
  schemaAt,
  sessionName,
  splitSessions,
} from "./model";
import {
  SERVER_LABELS,
  SERVER_NAMES,
  type ServerName,
  ServerChips,
  WatchCard,
} from "./Watch";

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
  server?: ServerName;
};
type Section = { name: string; description: string; advanced: boolean };
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
type Rec = Record<string, unknown>;
type Snapshot = {
  status?: Record<string, unknown>;
  sessions?: Rec[];
};
type Step = "category" | "mission" | "settings";
const WHERE_EVERY_MS = 10_000;

function App() {
  const [tests, setTests] = useState<Test[]>([]),
    [sections, setSections] = useState<Section[]>([]),
    [presets, setPresets] = useState<Preset[]>([]),
    [houses, setHouses] = useState<House[]>([]),
    [suites, setSuites] = useState<Suite[]>([]),
    [schema, setSchema] = useState<Schema | null>(null),
    [where, setWhere] = useState<Where>({}),
    [entries, setEntries] = useState<Entry[]>([]),
    [selected, setSelected] = useState(-1),
    [step, setStep] = useState<Step>("category"),
    [section, setSection] = useState(""),
    [name, setName] = useState("My missions"),
    [jobs, setJobs] = useState<Job[]>([]),
    [snapshot, setSnapshot] = useState<Snapshot>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [connected, setConnected] = useState(false),
    [tab, setTab] = useState<"configure" | "activity" | "history">("configure"),
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
      api<{ tests: Test[]; presets: Preset[]; houses?: House[]; sections?: Section[] }>(
        "/api/catalog",
      ),
      api<Schema>("/api/schema"),
      api<Suite[]>("/api/suites"),
    ])
      .then(([catalog, s, stored]) => {
        setTests(catalog.tests || []);
        setSections(catalog.sections || []);
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
  useEffect(() => {
    let alive = true;
    const load = () =>
      api<Where>("/api/where").then(
        (w) => {
          if (alive) setWhere(w);
        },
        () => {},
      );
    load();
    const timer = setInterval(load, WHERE_EVERY_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab, step]);
  const current = entries[selected],
    test = tests.find((t) => t.id === current?.test_id);
  const configOf = (entry: Entry | undefined) => {
    const t = tests.find((x) => x.id === entry?.test_id);
    const p = presets.find((x) => x.id === entry?.preset);
    return t ? mergeObjects(t.default_config, p?.config || {}) : {};
  };
  const displayConfig = configOf(current);
  useEffect(() => {
    setSeedText(current?.seeds?.join(", ") || "");
  }, [selected]);
  const update = (fn: (entry: Entry) => Entry) => {
    setEntries((old) => updateEntry(old, selected, fn));
    setJobs([]);
  };
  const add = (id: string) => {
    setEntries([...entries, { test_id: id }]);
    setSelected(entries.length);
    setStep("settings");
    setInvalid({});
    setJobs([]);
    setNotice("");
  };
  const edit = (i: number) => {
    setSelected(i);
    const t = tests.find((x) => x.id === entries[i]?.test_id);
    setSection(t ? sectionOf(t) : "");
    setStep("settings");
    setInvalid({});
  };
  const remove = (i: number) => {
    const next = entries.filter((_, j) => j !== i);
    setEntries(next);
    setInvalid({});
    setJobs([]);
    if (selected === i) {
      setSelected(-1);
      if (step === "settings") setStep(section ? "mission" : "category");
    } else if (selected > i) setSelected(selected - 1);
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
      const result = await api<{ jobs: Job[] }>("/api/resolve", "POST", { entries });
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
      setNotice(`Saved "${result.name || name}". Load it again under Saved sets.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const status = String(snapshot.status?.state || "unknown");
  const states: Record<ServerName, string> = {
    lab: status,
    play: String(snapshot.status?.play || "unknown"),
    arena: String(snapshot.status?.arena || "unknown"),
  };
  async function start() {
    if (!(await resolve())) return;
    try {
      await api("/api/start", "POST", { kind: "suite", entries });
      setTab("activity");
      const servers = new Set(
        entries.map((e) => tests.find((t) => t.id === e.test_id)?.server || "lab"),
      );
      const booting = SERVER_NAMES.filter(
        (which) => servers.has(which) && states[which] !== "running",
      );
      setNotice(
        booting.length
          ? `Starting the ${booting.join(" and ")} server first (a minute or two), then your missions run.`
          : "Started. Follow along here, or join the server below to watch in Minecraft.",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function startServer(which: ServerName) {
    try {
      await api("/api/start", "POST", {
        kind: "setup",
        operation: which === "lab" ? "server-start" : `${which}-start`,
      });
      setNotice(`Starting the ${which} server. Its badge says running when it's ready.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function action(name: string) {
    try {
      const result = await api<{ status?: string }>(`/api/${name}`, "POST", {});
      setNotice(result.status || `${humanize(name)} requested.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const sectionOf = (t: Test) => t.section || humanize(t.family);
  const shownSections = useMemo(() => {
    const named = new Map(sections.map((s) => [s.name, s]));
    const order = [
      ...sections.map((s) => s.name),
      ...tests.map(sectionOf).filter((n) => !named.has(n)),
    ];
    return Array.from(new Set(order))
      .map((n) => ({
        name: n,
        description: named.get(n)?.description || "",
        advanced:
          named.get(n)?.advanced ??
          tests.filter((t) => sectionOf(t) === n).every((t) => t.advanced),
        count: tests.filter((t) => sectionOf(t) === n).length,
      }))
      .filter((s) => s.count > 0);
  }, [sections, tests]);
  const basic = test?.basic_fields || ["repetitions"];
  const presetsFor = (t: Test) =>
    presets.filter((p) => !p.supported_tests || p.supported_tests.includes(t.family));
  /** "Crash site · Mixed 10 · Matches 1": the mission's main settings. */
  const summary = (entry: Entry) => {
    const t = tests.find((x) => x.id === entry.test_id);
    if (!t || !schema) return "";
    const config = configOf(entry);
    return (t.basic_fields || [])
      .filter((p) => p.startsWith("run."))
      .map((p) => {
        const at = p.split(".");
        const own = getPath(entry.overrides || {}, at);
        const v = own !== undefined ? own : getPath(config, at);
        const node = schemaAt(schema, at);
        const label = node ? fieldText(nonNullSchema(node), t.family).label : "";
        const custom = node?.custom;
        if ((v === null || v === undefined) && custom) {
          const field = [...at.slice(0, -1), custom.field];
          const mine = getPath(entry.overrides || {}, field) ?? getPath(config, field);
          return typeof mine === "string" && mine ? `${label}: ${mine}` : "";
        }
        if (v === null || v === undefined || Array.isArray(v)) return "";
        if (typeof v === "string")
          return humanize(v.split(/[\\/]/).pop()!.replace(/\.json$/, ""));
        return `${label} ${v}`;
      })
      .filter(Boolean)
      .join(" · ");
  };
  const sectionInfo = shownSections.find((s) => s.name === section);
  const blocked = !!Object.keys(invalid).length;
  const { active, waiting, recent, hidden } = splitSessions(snapshot.sessions || []);
  const activeJobs = active.flatMap((s) => (s.jobs as Rec[] | undefined) || []);
  const runningJob =
    activeJobs.find((j) => j.status === "running") || activeJobs[0] || null;
  const queued = [...activeJobs.filter((j) => j.status === "queued"), ...waiting];
  const crumbs = (
    <nav className="crumbs" aria-label="Where you are">
      <button onClick={() => setStep("category")} disabled={step === "category"}>
        What to test
      </button>
      {step !== "category" && section && (
        <>
          <span>›</span>
          <button onClick={() => setStep("mission")} disabled={step === "mission"}>
            {section}
          </button>
        </>
      )}
      {step === "settings" && test && (
        <>
          <span>›</span>
          <span className="here">{test.label}</span>
        </>
      )}
    </nav>
  );
  return (
    <SchemaCtx.Provider value={schema || {}}>
      <WhereCtx.Provider value={where}>
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
              <nav aria-label="Workspace">
                <button
                  className={tab === "configure" ? "active" : ""}
                  onClick={() => setTab("configure")}
                >
                  ◇ <span>Run something</span>
                </button>
                <button
                  className={tab === "activity" ? "active" : ""}
                  onClick={() => setTab("activity")}
                >
                  ◉ <span>Live activity</span>
                </button>
                <button
                  className={tab === "history" ? "active" : ""}
                  onClick={() => setTab("history")}
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
              <div className="content">
                <div className="heading">
                  <div>
                    <h1>
                      {tab === "configure"
                        ? "Send the bot on missions"
                        : tab === "activity"
                          ? "Live activity"
                          : "History & builds"}
                    </h1>
                    <p>
                      {tab === "configure"
                        ? "Choose what to test, pick a mission, adjust it if you like, then press Start."
                        : tab === "activity"
                          ? "What's running now and how to watch it in Minecraft."
                          : "Past results and house builds."}
                    </p>
                  </div>
                  <ServerChips
                    states={states}
                    where={where}
                    start={startServer}
                  />
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
                  <div className="workspace">
                    <Card className="panel wizard">
                      {crumbs}
                      {(step === "category" || (step === "mission" && !sectionInfo)) && (
                        <>
                          <h2>What do you want to test?</h2>
                          <div className="cards">
                            {shownSections
                              .filter((s) => showAdvanced || !s.advanced)
                              .map((s) => (
                                <button
                                  key={s.name}
                                  className="pick-card"
                                  onClick={() => {
                                    setSection(s.name);
                                    setStep("mission");
                                  }}
                                >
                                  <strong>{s.name}</strong>
                                  <small>{s.description}</small>
                                  <span className="count">
                                    {s.count} mission{s.count === 1 ? "" : "s"}
                                  </span>
                                </button>
                              ))}
                          </div>
                          {shownSections.some((s) => s.advanced) && (
                            <button
                              className="advanced-toggle"
                              aria-expanded={showAdvanced}
                              onClick={() => setShowAdvanced(!showAdvanced)}
                            >
                              {showAdvanced ? "Hide developer tools" : "Show developer tools"}
                            </button>
                          )}
                        </>
                      )}
                      {step === "mission" && sectionInfo && (
                        <>
                          <h2>{sectionInfo.name}</h2>
                          <p className="lede">{sectionInfo.description}</p>
                          <div className="cards">
                            {tests
                              .filter((t) => sectionOf(t) === section)
                              .map((t) => {
                                const picked = entries.filter((e) => e.test_id === t.id).length;
                                return (
                                  <button key={t.id} className="pick-card" onClick={() => add(t.id)}>
                                    <strong>{t.label}</strong>
                                    <small>{t.description}</small>
                                    <span className="count">
                                      {SERVER_LABELS[t.server || "lab"]}
                                      {picked ? ` · in your list${picked > 1 ? ` ×${picked}` : ""}` : ""}
                                    </span>
                                  </button>
                                );
                              })}
                          </div>
                        </>
                      )}
                      {step === "settings" && (!current || !test || !schema) && (
                        <div className="empty">
                          Nothing to set up yet.{" "}
                          <button className="link" onClick={() => setStep("category")}>
                            Choose what to test
                          </button>
                        </div>
                      )}
                      {step === "settings" && current && test && schema && (
                        <>
                          <h2>{test.label}</h2>
                          <p className="lede">{test.description}</p>
                          <div className="fields">
                            {basic
                              .filter((p) => p.startsWith("run."))
                              .map((p) => (
                                <Fields
                                  key={selected + p}
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
                                  A seed picks the random layout. Same seed, same layout, so
                                  runs can be compared. Each seed you list is one run.
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
                                  1 to 3. Running more than once shows whether it succeeds
                                  every time or just got lucky.
                                </small>
                              </div>
                            )}
                          </div>
                          <details className="advanced-settings">
                            <summary>More settings</summary>
                            {presetsFor(test).length > 0 && (
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
                                    <option value="">None (the usual settings)</option>
                                    {presetsFor(test).map((p) => (
                                      <option value={p.id} key={p.id}>
                                        {p.label}
                                      </option>
                                    ))}
                                  </select>
                                  <small>A saved bundle of settings to start from.</small>
                                </div>
                              </div>
                            )}
                            <Fields
                              key={selected}
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
                            {showAdvanced &&
                              ["body", "planner", "model"].map((key) => (
                                <Fields
                                  key={selected + key}
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
                          <div className="wizard-actions">
                            <Button variant="secondary" onClick={() => setStep("category")}>
                              + Add another mission
                            </Button>
                            <Button disabled={blocked} onClick={start}>
                              ▶ &nbsp;Start {entries.length > 1 ? `all ${entries.length}` : ""}
                            </Button>
                          </div>
                        </>
                      )}
                    </Card>
                    <Card
                      className="panel queue"
                      title="Your run list"
                      description="Runs one after another, top to bottom."
                    >
                      {entries.length ? (
                        <ol className="run-list">
                          {entries.map((entry, i) => {
                            const t = tests.find((x) => x.id === entry.test_id);
                            return (
                              <li key={i} className={selected === i && step === "settings" ? "selected" : ""}>
                                <button className="run-item" onClick={() => edit(i)}>
                                  <strong>{t?.label || entry.test_id}</strong>
                                  <small>{summary(entry) || "Usual settings"}</small>
                                </button>
                                <button
                                  className="link"
                                  aria-label={`Remove ${t?.label || entry.test_id}`}
                                  onClick={() => remove(i)}
                                >
                                  ✕
                                </button>
                              </li>
                            );
                          })}
                        </ol>
                      ) : (
                        <div className="empty">Nothing yet. Choose what to test on the left.</div>
                      )}
                      <div className="queue-actions">
                        <Button disabled={!entries.length || blocked} onClick={start}>
                          ▶ &nbsp;Start
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={!entries.length || blocked}
                          onClick={resolve}
                        >
                          Check run list
                        </Button>
                      </div>
                      {jobs.length > 0 && (
                        <ol className="job-preview">
                          {jobs.map((job, i) => (
                            <li key={job.id || i}>
                              <span>
                                <strong>{job.label || job.kind || job.id}</strong>
                                <small>{job.entrant || job.kind}</small>
                              </span>
                              <details>
                                <summary>Exact settings</summary>
                                <pre>{JSON.stringify(job.run_config, null, 2)}</pre>
                              </details>
                            </li>
                          ))}
                        </ol>
                      )}
                      <details className="saved-sets">
                        <summary>Saved sets</summary>
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
                                  const t = tests.find(
                                    (x) => x.id === loaded.entries[loaded.selected]?.test_id,
                                  );
                                  if (t) {
                                    setSection(sectionOf(t));
                                    setStep("settings");
                                  }
                                }
                              }}
                            >
                              Load set
                            </Button>
                          </div>
                        )}
                        <label className="suite-name">
                          Name (to save this list for later)
                          <input value={name} onChange={(e) => setName(e.target.value)} />
                        </label>
                        <Button
                          variant="secondary"
                          disabled={!entries.length || !name.trim() || blocked}
                          onClick={save}
                        >
                          Save set
                        </Button>
                      </details>
                    </Card>
                  </div>
                )}
                {tab === "activity" && (
                  <div className="activity">
                    <Card className="panel" title="Where to watch">
                      <WatchCard job={runningJob} where={where} />
                    </Card>
                    <Card className="panel" title="Now running">
                      {active.length ? (
                        active.map((s, i) => <LiveSession key={String(s.id || i)} session={s} />)
                      ) : (
                        <div className="empty">
                          Nothing is running.{" "}
                          <button className="link" onClick={() => setTab("configure")}>
                            Run something
                          </button>
                        </div>
                      )}
                      <div className="actions">
                        <Button variant="secondary" onClick={() => action("stop")}>
                          Stop after current batch
                        </Button>
                        <Button variant="danger" onClick={() => action("kill")}>
                          Cancel current run (cleanup first)
                        </Button>
                      </div>
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
                        {hidden > 0 && <p>Older runs are on the History tab.</p>}
                      </Card>
                    )}
                    <details className="danger-zone">
                      <summary>Dashboard</summary>
                      <p>Shutting the dashboard down leaves running batches going.</p>
                      <Button variant="ghost" onClick={() => action("shutdown")}>
                        Shut down dashboard
                      </Button>
                    </details>
                  </div>
                )}
                {tab === "history" && <History />}
              </div>
            </main>
          </div>
        </HousesCtx.Provider>
      </WhereCtx.Provider>
    </SchemaCtx.Provider>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
