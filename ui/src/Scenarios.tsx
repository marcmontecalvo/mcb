// Saved versioned arena scenarios: duplicate & edit one factor, swap spawn sides,
// and compare baseline vs variant side-by-side with linked run details and
// strictly separated failed / abandoned starts.
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card } from "@homenet/ui";
import { api } from "./api";
import {
  applyConfigEdit,
  diffScenarioConfigs,
  formatDelta,
  formatDuration,
  formatPct,
  formatValue,
  sideSwapMapping,
  swapScenarioSides,
  type ComparisonResult,
  type RunEntry,
  type ScenarioConfig,
  type ScenarioRecord,
} from "./scenarios";

const EDITABLE_FACTORS = [
  { value: "kit", label: "Global kit" },
  { value: "kit_mode", label: "Kit mode" },
  { value: "respawn_mode", label: "Respawn mode" },
  { value: "respawn_count", label: "Extra respawns" },
  { value: "team_kits.red", label: "Red team kit" },
  { value: "team_kits.blue", label: "Blue team kit" },
  { value: "arena", label: "Arena map" },
  { value: "game", label: "Game mode" },
  { value: "count", label: "Matches per series" },
  { value: "timeout", label: "Timeout (seconds)" },
  { value: "__custom__", label: "Custom dot-path…" },
];

const DEFAULT_NEW_CONFIG: ScenarioConfig = {
  arena: "crossroads",
  game: "teams",
  teams: { red: ["MCBot"], blue: ["MCBot2"] },
  team_mobs: {},
  team_mob_kits: {},
  waves: [],
  respawn_mode: "elimination",
  respawn_count: 0,
  kit: "fighter",
  team_kits: {},
  kit_mode: "uniform",
  kit_pool: [],
  bot_profiles: {},
  count: 3,
  timeout: 300,
};

function parseFactorInput(field: string, raw: string): unknown {
  const trimmed = raw.trim();
  if (field === "respawn_count" || field === "count" || field === "timeout") {
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : trimmed;
  }
  if (
    trimmed === "true" ||
    trimmed === "false" ||
    trimmed === "null" ||
    trimmed.startsWith("{") ||
    trimmed.startsWith("[")
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed;
    }
  }
  return trimmed;
}

function RunDetail({ path }: { path: string }) {
  const [value, setValue] = useState<unknown>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setValue(null);
    setError("");
    if (!path) {
      setError("No artifact path recorded for this run.");
      return () => {
        active = false;
      };
    }
    api(`/api/detail?path=${encodeURIComponent(path)}`).then(
      (data) => {
        if (active) setValue(data);
      },
      (err) => {
        if (active) setError(String(err?.message ?? err));
      },
    );
    return () => {
      active = false;
    };
  }, [path]);
  if (error) return <p className="field-error">{error}</p>;
  if (!value) return <p>Loading run artifact…</p>;
  return (
    <>
      <p>
        Artifact: <code>{path}</code>
      </p>
      <pre className="detail">{JSON.stringify(value, null, 2)}</pre>
    </>
  );
}

function RunsTable({
  runs,
  emptyText,
  tone,
}: {
  runs: RunEntry[];
  emptyText: string;
  tone: "ready" | "attention" | "quiet";
}) {
  const [openKey, setOpenKey] = useState("");
  if (runs.length === 0) {
    return <p className="empty">{emptyText}</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Outcome</th>
            <th>Winner (logical)</th>
            <th>Sides</th>
            <th>Duration</th>
            <th>Reason / Error</th>
            <th>Artifact</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, idx) => {
            const key = `${run.run_id || run.path || idx}`;
            const isOpen = openKey === key;
            return [
              <tr
                key={key}
                className={"clickable" + (isOpen ? " open" : "")}
                tabIndex={0}
                onClick={() => setOpenKey(isOpen ? "" : key)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setOpenKey(isOpen ? "" : key);
                }}
              >
                <td>
                  <strong>{run.run_id || `#${idx + 1}`}</strong>
                  {run.scenario_version ? ` (v${run.scenario_version})` : ""}
                </td>
                <td>
                  <Badge tone={tone}>{run.outcome}</Badge>
                </td>
                <td>
                  {run.logical_winner
                    ? run.swapped && run.winner && run.winner !== run.logical_winner
                      ? `${run.logical_winner} (on ${run.winner})`
                      : run.logical_winner
                    : "—"}
                </td>
                <td>{run.swapped ? "Swapped" : "Standard"}</td>
                <td>{formatDuration(run.duration_s)}</td>
                <td>{run.reason || "—"}</td>
                <td>
                  <code>{run.path || "—"}</code>
                </td>
              </tr>,
              isOpen && (
                <tr key={`${key}:detail`} className="detail-row">
                  <td colSpan={7}>
                    <RunDetail path={run.path} />
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Scenarios() {
  const [scenarios, setScenarios] = useState<ScenarioRecord[] | null>(null);
  const [unavailable, setUnavailable] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // Save new scenario form
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newArena, setNewArena] = useState(DEFAULT_NEW_CONFIG.arena);
  const [newGame, setNewGame] = useState(DEFAULT_NEW_CONFIG.game);
  const [newRedTeam, setNewRedTeam] = useState("MCBot");
  const [newBlueTeam, setNewBlueTeam] = useState("MCBot2");
  const [newKit, setNewKit] = useState("fighter");
  const [newRespawnMode, setNewRespawnMode] = useState("elimination");
  const [newRespawnCount, setNewRespawnCount] = useState("0");
  const [newFactorNote, setNewFactorNote] = useState("");

  // Duplicate & edit one factor
  const [dupSourceId, setDupSourceId] = useState("");
  const [dupName, setDupName] = useState("");
  const [dupField, setDupField] = useState("kit");
  const [dupCustomField, setDupCustomField] = useState("");
  const [dupValue, setDupValue] = useState("");
  const [dupNote, setDupNote] = useState("");

  // Side-by-side comparison
  const [baselineId, setBaselineId] = useState("");
  const [variantId, setVariantId] = useState("");
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [comparing, setComparing] = useState(false);

  const loadScenarios = () => {
    setError("");
    setUnavailable("");
    api<ScenarioRecord[]>("/api/scenarios").then(
      (rows) => {
        const list = Array.isArray(rows) ? rows : [];
        setScenarios(list);
        if (list.length >= 1 && !baselineId) setBaselineId(list[0].id);
        if (list.length >= 2 && !variantId) setVariantId(list[1].id);
      },
      (err) => {
        const msg = String(err?.message ?? err);
        setScenarios([]);
        if (/404|not found/i.test(msg)) {
          setUnavailable(
            "The /api/scenarios backend endpoints are not active yet on this dashboard server.",
          );
        } else {
          setError(msg);
        }
      },
    );
  };

  useEffect(() => {
    loadScenarios();
  }, []);

  const dupSource = (scenarios ?? []).find((s) => s.id === dupSourceId) ?? null;
  const effectiveDupField = dupField === "__custom__" ? dupCustomField.trim() : dupField;
  let dupPreviewDiff: ReturnType<typeof diffScenarioConfigs> = [];
  if (dupSource && effectiveDupField && dupValue.trim() !== "") {
    try {
      const edited = applyConfigEdit(
        dupSource.config,
        effectiveDupField,
        parseFactorInput(effectiveDupField, dupValue),
      );
      dupPreviewDiff = diffScenarioConfigs(dupSource.config, edited);
    } catch {
      dupPreviewDiff = [];
    }
  }

  const handleSaveNew = async () => {
    setError("");
    setNotice("");
    const splitNames = (text: string) =>
      text
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    const teams: Record<string, string[]> =
      newGame === "waves"
        ? {}
        : {
            ...(splitNames(newRedTeam).length ? { red: splitNames(newRedTeam) } : {}),
            ...(splitNames(newBlueTeam).length ? { blue: splitNames(newBlueTeam) } : {}),
          };
    const config: ScenarioConfig = {
      ...DEFAULT_NEW_CONFIG,
      arena: newArena.trim() || "crossroads",
      game: newGame,
      teams,
      kit: newKit.trim() || "fighter",
      respawn_mode: newRespawnMode,
      respawn_count: Math.max(0, Number(newRespawnCount) || 0),
    };
    try {
      const saved = await api<ScenarioRecord>("/api/scenarios", "POST", {
        name: newName.trim(),
        factor_note: newFactorNote.trim(),
        config,
      });
      setNotice(`Saved scenario "${saved.name}" (v${saved.version}).`);
      setNewName("");
      setNewFactorNote("");
      setShowCreate(false);
      loadScenarios();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    }
  };

  const handleDuplicate = async () => {
    if (!dupSource) return;
    setError("");
    setNotice("");
    const edits: Record<string, unknown> = {};
    if (effectiveDupField && dupValue.trim() !== "") {
      edits[effectiveDupField] = parseFactorInput(effectiveDupField, dupValue);
    }
    try {
      const created = await api<ScenarioRecord>(
        `/api/scenarios/${encodeURIComponent(dupSource.id)}/duplicate`,
        "POST",
        {
          name: dupName.trim() || `${dupSource.name} Variant`,
          factor_note: dupNote.trim(),
          edits,
        },
      );
      setNotice(
        `Created variant "${created.name}" (${created.changed_fields?.length ?? 0} changed factor(s)).`,
      );
      setBaselineId(dupSource.id);
      setVariantId(created.id);
      setDupSourceId("");
      setDupName("");
      setDupValue("");
      setDupNote("");
      loadScenarios();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    }
  };

  const handleSwapSides = async (scenario: ScenarioRecord) => {
    setError("");
    setNotice("");
    try {
      // Validate locally first so non-team scenarios show a clear message immediately.
      sideSwapMapping(scenario.config);
      const created = await api<ScenarioRecord>(
        `/api/scenarios/${encodeURIComponent(scenario.id)}/swap`,
        "POST",
        {},
      );
      setNotice(`Created side-swapped scenario "${created.name}".`);
      loadScenarios();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    }
  };

  const handleCompare = async () => {
    if (!baselineId || !variantId) return;
    setError("");
    setComparing(true);
    try {
      const res = await api<ComparisonResult>("/api/scenarios/compare", "POST", {
        baseline_id: baselineId,
        variant_id: variantId,
      });
      setComparison(res);
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    } finally {
      setComparing(false);
    }
  };

  const allTeams = comparison
    ? Array.from(
        new Set([
          ...Object.keys(comparison.baseline.wins_by_team),
          ...Object.keys(comparison.variant.wins_by_team),
          ...Object.keys(comparison.baseline.damage_dealt_by_team),
          ...Object.keys(comparison.variant.damage_dealt_by_team),
        ]),
      ).sort()
    : [];

  return (
    <div className="history">
      {unavailable && (
        <Alert tone="quiet" title="Scenarios API unavailable">
          {unavailable}
        </Alert>
      )}
      {error && <p className="field-error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <Card
        className="panel"
        title="Saved arena scenarios"
        description="Versioned snapshots of map, roster, allied mobs, kits, bot profiles, and respawn rules. Duplicate to vary one factor or swap spawn sides."
      >
        <div className="history-tools">
          <Button variant="primary" onClick={() => setShowCreate(!showCreate)}>
            {showCreate ? "Close new scenario" : "+ Save new scenario"}
          </Button>
          <Button variant="secondary" onClick={loadScenarios}>
            Refresh
          </Button>
        </div>

        {showCreate && (
          <div className="field-group">
            <h4>New scenario snapshot</h4>
            <div className="fields">
              <div className="field">
                <label htmlFor="sc-name">Scenario name</label>
                <input
                  id="sc-name"
                  placeholder="e.g. Crossroads 1v1 Fighter Baseline"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="sc-arena">Arena map</label>
                <input
                  id="sc-arena"
                  value={newArena}
                  onChange={(e) => setNewArena(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="sc-game">Game mode</label>
                <select
                  id="sc-game"
                  value={newGame}
                  onChange={(e) => setNewGame(e.target.value)}
                >
                  <option value="teams">PvP (teams)</option>
                  <option value="waves">Co-op (waves)</option>
                  <option value="race">Kill race (race)</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="sc-kit">Kit</label>
                <input
                  id="sc-kit"
                  value={newKit}
                  onChange={(e) => setNewKit(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="sc-respawn-mode">Respawn mode</label>
                <select
                  id="sc-respawn-mode"
                  value={newRespawnMode}
                  onChange={(e) => setNewRespawnMode(e.target.value)}
                >
                  <option value="elimination">Elimination</option>
                  <option value="player">Per participant</option>
                  <option value="team">Shared team pool</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="sc-respawn-count">Extra respawns</label>
                <input
                  id="sc-respawn-count"
                  type="number"
                  min={0}
                  value={newRespawnCount}
                  onChange={(e) => setNewRespawnCount(e.target.value)}
                />
              </div>
              {newGame !== "waves" && (
                <>
                  <div className="field">
                    <label htmlFor="sc-red">Red team roster (comma-separated)</label>
                    <input
                      id="sc-red"
                      value={newRedTeam}
                      onChange={(e) => setNewRedTeam(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="sc-blue">Blue team roster (comma-separated)</label>
                    <input
                      id="sc-blue"
                      value={newBlueTeam}
                      onChange={(e) => setNewBlueTeam(e.target.value)}
                    />
                  </div>
                </>
              )}
              <div className="field wide">
                <label htmlFor="sc-note">Factor / baseline note</label>
                <input
                  id="sc-note"
                  placeholder="Describe this baseline or tested factor"
                  value={newFactorNote}
                  onChange={(e) => setNewFactorNote(e.target.value)}
                />
              </div>
            </div>
            <div className="actions">
              <Button
                variant="primary"
                disabled={!newName.trim()}
                onClick={handleSaveNew}
              >
                Save scenario
              </Button>
            </div>
          </div>
        )}

        {scenarios === null ? (
          <p>Loading scenarios…</p>
        ) : scenarios.length === 0 ? (
          <div className="empty">No saved scenarios yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Version</th>
                  <th>Map & Rules</th>
                  <th>Note & Provenance</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.map((s) => {
                  let swapPreview = "";
                  try {
                    const swapped = swapScenarioSides(s.config);
                    swapPreview = Object.entries(swapped.teams)
                      .map(([t, members]) => `${t}: ${members.join(", ")}`)
                      .join(" · ");
                  } catch {
                    swapPreview = "";
                  }
                  return (
                    <tr key={s.id}>
                      <td>
                        <strong>{s.name}</strong>
                        <div>
                          <code>{s.id}</code>
                        </div>
                      </td>
                      <td>
                        <Badge tone="quiet">v{s.version}</Badge>
                        {s.history.length > 0 && (
                          <details>
                            <summary>{s.history.length} prior</summary>
                            <ul>
                              {s.history.map((h) => (
                                <li key={h.version}>
                                  v{h.version}: {h.factor_note || h.updated_at}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </td>
                      <td>
                        <div>
                          {s.config.arena} · {s.config.game} · {s.config.respawn_mode}
                          {s.config.respawn_count > 0
                            ? ` (+${s.config.respawn_count})`
                            : ""}
                        </div>
                        <small>
                          Kit: {formatValue(s.config.kit)} ({s.config.kit_mode})
                        </small>
                      </td>
                      <td>
                        <div>{s.factor_note || "—"}</div>
                        {s.parent_id && (
                          <Badge tone="quiet">from {s.parent_id}</Badge>
                        )}{" "}
                        {s.swapped_from && (
                          <Badge tone="working">sides swapped</Badge>
                        )}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                          <Button
                            variant="secondary"
                            onClick={() => {
                              setDupSourceId(s.id);
                              setDupName(`${s.name} Variant`);
                            }}
                          >
                            Duplicate & edit
                          </Button>
                          {swapPreview && (
                            <Button
                              variant="secondary"
                              title={`Swap preview: ${swapPreview}`}
                              onClick={() => handleSwapSides(s)}
                            >
                              Swap sides
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {dupSource && (
          <div className="field-group">
            <h4>Duplicate “{dupSource.name}” & edit one factor</h4>
            <div className="fields">
              <div className="field">
                <label htmlFor="dup-name">Variant name</label>
                <input
                  id="dup-name"
                  value={dupName}
                  onChange={(e) => setDupName(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="dup-field">Factor to change</label>
                <select
                  id="dup-field"
                  value={dupField}
                  onChange={(e) => setDupField(e.target.value)}
                >
                  {EDITABLE_FACTORS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </div>
              {dupField === "__custom__" && (
                <div className="field">
                  <label htmlFor="dup-custom">Dot-path field</label>
                  <input
                    id="dup-custom"
                    placeholder="e.g. bot_profiles.red"
                    value={dupCustomField}
                    onChange={(e) => setDupCustomField(e.target.value)}
                  />
                </div>
              )}
              <div className="field">
                <label htmlFor="dup-value">New value</label>
                <input
                  id="dup-value"
                  placeholder="e.g. archer or 2"
                  value={dupValue}
                  onChange={(e) => setDupValue(e.target.value)}
                />
              </div>
              <div className="field wide">
                <label htmlFor="dup-note">Factor note</label>
                <input
                  id="dup-note"
                  placeholder="Why you changed this single factor"
                  value={dupNote}
                  onChange={(e) => setDupNote(e.target.value)}
                />
              </div>
            </div>
            {dupPreviewDiff.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Changed factor preview</th>
                      <th>Baseline</th>
                      <th>Variant</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dupPreviewDiff.map((d) => (
                      <tr key={d.field}>
                        <td>
                          <code>{d.field}</code>
                        </td>
                        <td>{formatValue(d.baseline)}</td>
                        <td>{formatValue(d.variant)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="actions">
              <Button variant="primary" onClick={handleDuplicate}>
                Create variant
              </Button>
              <Button variant="secondary" onClick={() => setDupSourceId("")}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card
        className="panel"
        title="Side-by-side comparison"
        description="Compare wins/completed rounds, duration, and damage across baseline and variant series. Failed and abandoned starts are reported separately and never dilute win rates."
      >
        <div className="fields">
          <div className="field">
            <label htmlFor="cmp-baseline">Baseline scenario</label>
            <select
              id="cmp-baseline"
              value={baselineId}
              onChange={(e) => setBaselineId(e.target.value)}
            >
              <option value="">Select baseline…</option>
              {(scenarios ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} (v{s.version})
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="cmp-variant">Variant scenario</label>
            <select
              id="cmp-variant"
              value={variantId}
              onChange={(e) => setVariantId(e.target.value)}
            >
              <option value="">Select variant…</option>
              {(scenarios ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} (v{s.version})
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="actions">
          <Button
            variant="primary"
            disabled={!baselineId || !variantId || comparing}
            onClick={handleCompare}
          >
            {comparing ? "Comparing…" : "Compare scenarios"}
          </Button>
        </div>

        {comparison && (
          <div className="field-group">
            {comparison.changed_fields.length > 0 && (
              <>
                <h4>Changed factors</h4>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>Baseline</th>
                        <th>Variant</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.changed_fields.map((d) => (
                        <tr key={d.field}>
                          <td>
                            <code>{d.field}</code>
                          </td>
                          <td>{formatValue(d.baseline)}</td>
                          <td>{formatValue(d.variant)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            <h4>Completed rounds summary</h4>
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", margin: "8px 0" }}>
              <Badge tone="ready">
                Baseline completed: {comparison.baseline.completed_rounds}
              </Badge>
              <Badge tone="ready">
                Variant completed: {comparison.variant.completed_rounds}
              </Badge>
              <Badge
                tone={
                  comparison.baseline.failed_starts_count +
                    comparison.variant.failed_starts_count >
                  0
                    ? "attention"
                    : "quiet"
                }
              >
                Failed starts: {comparison.baseline.failed_starts_count} vs{" "}
                {comparison.variant.failed_starts_count}
              </Badge>
              <Badge
                tone={
                  comparison.baseline.abandoned_starts_count +
                    comparison.variant.abandoned_starts_count >
                  0
                    ? "attention"
                    : "quiet"
                }
              >
                Abandoned starts: {comparison.baseline.abandoned_starts_count} vs{" "}
                {comparison.variant.abandoned_starts_count}
              </Badge>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Baseline ({comparison.baseline.scenario?.name ?? baselineId})</th>
                    <th>Variant ({comparison.variant.scenario?.name ?? variantId})</th>
                    <th>Delta</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Completed rounds</td>
                    <td>{comparison.baseline.completed_rounds}</td>
                    <td>{comparison.variant.completed_rounds}</td>
                    <td>{formatDelta(comparison.deltas.completed_rounds)}</td>
                  </tr>
                  <tr>
                    <td>Draws</td>
                    <td>{comparison.baseline.draws}</td>
                    <td>{comparison.variant.draws}</td>
                    <td>
                      {formatDelta(comparison.variant.draws - comparison.baseline.draws)}
                    </td>
                  </tr>
                  <tr>
                    <td>Median duration</td>
                    <td>{formatDuration(comparison.baseline.median_duration_s)}</td>
                    <td>{formatDuration(comparison.variant.median_duration_s)}</td>
                    <td>{formatDelta(comparison.deltas.median_duration_s, " s")}</td>
                  </tr>
                  <tr>
                    <td>Mean duration</td>
                    <td>{formatDuration(comparison.baseline.mean_duration_s)}</td>
                    <td>{formatDuration(comparison.variant.mean_duration_s)}</td>
                    <td>{formatDelta(comparison.deltas.mean_duration_s, " s")}</td>
                  </tr>
                  {allTeams.map((team) => (
                    <tr key={`wr-${team}`}>
                      <td>{team} wins / rate</td>
                      <td>
                        {comparison.baseline.wins_by_team[team] ?? 0} (
                        {formatPct(comparison.baseline.win_rate_by_team[team])})
                      </td>
                      <td>
                        {comparison.variant.wins_by_team[team] ?? 0} (
                        {formatPct(comparison.variant.win_rate_by_team[team])})
                      </td>
                      <td>
                        {comparison.deltas.win_rate_by_team[team] !== undefined
                          ? `${comparison.deltas.win_rate_by_team[team] > 0 ? "+" : ""}${Math.round(comparison.deltas.win_rate_by_team[team] * 100)} pp`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                  {allTeams.map((team) => (
                    <tr key={`dmg-${team}`}>
                      <td>{team} damage dealt / taken</td>
                      <td>
                        {(comparison.baseline.damage_dealt_by_team[team] ?? 0).toFixed(1)}{" "}
                        / {(comparison.baseline.damage_taken_by_team[team] ?? 0).toFixed(1)}
                      </td>
                      <td>
                        {(comparison.variant.damage_dealt_by_team[team] ?? 0).toFixed(1)}{" "}
                        / {(comparison.variant.damage_taken_by_team[team] ?? 0).toFixed(1)}
                      </td>
                      <td>
                        {formatDelta(comparison.deltas.damage_dealt_by_team[team])} dealt
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <td>Spawn-side balance (unswapped / swapped rounds)</td>
                    <td>
                      {comparison.baseline.per_side_breakdown.unswapped.completed_rounds}{" "}
                      std /{" "}
                      {comparison.baseline.per_side_breakdown.swapped.completed_rounds}{" "}
                      swapped
                    </td>
                    <td>
                      {comparison.variant.per_side_breakdown.unswapped.completed_rounds}{" "}
                      std /{" "}
                      {comparison.variant.per_side_breakdown.swapped.completed_rounds}{" "}
                      swapped
                    </td>
                    <td>—</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <h4>Failed starts (excluded from win rates & duration)</h4>
            <RunsTable
              runs={[
                ...comparison.baseline.failed_starts,
                ...comparison.variant.failed_starts,
              ]}
              emptyText="No failed starts."
              tone="attention"
            />

            <h4>Abandoned starts (excluded from win rates & duration)</h4>
            <RunsTable
              runs={[
                ...comparison.baseline.abandoned_starts,
                ...comparison.variant.abandoned_starts,
              ]}
              emptyText="No abandoned starts."
              tone="attention"
            />

            <h4>Completed runs (click any row to inspect run artifact)</h4>
            <RunsTable
              runs={[
                ...comparison.baseline.completed_runs,
                ...comparison.variant.completed_runs,
              ]}
              emptyText="No completed runs recorded for these scenarios yet."
              tone="ready"
            />
          </div>
        )}
      </Card>
    </div>
  );
}
