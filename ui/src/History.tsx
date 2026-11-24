// Past results and house builds as tables; a row opens its full summary.
import { useEffect, useState } from "react";
import { Badge, Button, Card } from "@homenet/ui";
import { api } from "./api";
import { buildRow, resultRow } from "./forms";

type Rec = Record<string, unknown>;
const PAGE = 40;
const when = (d: Date | null) =>
  d ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

function Detail({ path }: { path: string }) {
  const [value, setValue] = useState<unknown>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api(`/api/detail?path=${encodeURIComponent(path)}`).then(setValue, (e) =>
      setError(String(e.message)),
    );
  }, [path]);
  if (error) return <p className="field-error">{error}</p>;
  if (!value) return <p>Loading…</p>;
  return (
    <>
      <p>
        File: <code>{path}</code>
      </p>
      <pre className="detail">{JSON.stringify(value, null, 2)}</pre>
    </>
  );
}

export function History() {
  const [results, setResults] = useState<Rec[] | null>(null);
  const [builds, setBuilds] = useState<Rec[] | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState("");
  const load = () => {
    setError("");
    Promise.all([api<Rec[]>("/api/results"), api<Rec[]>("/api/builds")]).then(
      ([r, b]) => {
        setResults(r);
        setBuilds(b);
      },
      (e) => setError(String(e.message)),
    );
  };
  useEffect(() => {
    load();
  }, []);
  const rows = (results || [])
    .map((r) => ({ raw: r, ...resultRow(r) }))
    .filter((r) =>
      `${r.what} ${r.raw.body ?? ""} ${r.raw.status ?? ""}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
    );
  return (
    <div className="history">
      {error && <p className="field-error">{error}</p>}
      <Card className="panel" title="Results" description="Every finished run, newest first. Click a row for its full summary.">
        <div className="history-tools">
          <input
            aria-label="Filter results"
            placeholder="Filter, e.g. arena, crafting, failed"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setShown(PAGE);
            }}
          />
          <Button variant="secondary" onClick={load}>
            Refresh
          </Button>
        </div>
        {results === null ? (
          <p>Loading…</p>
        ) : rows.length === 0 ? (
          <div className="empty">No results{filter ? " match that filter" : " yet"}.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>What</th>
                  <th>Who</th>
                  <th>Outcome</th>
                  <th>Median time</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((r) => {
                  const id = String(r.raw.id || r.path);
                  return [
                    <tr
                      key={id}
                      className={"clickable" + (open === id ? " open" : "")}
                      tabIndex={0}
                      onClick={() => setOpen(open === id ? "" : id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") setOpen(open === id ? "" : id);
                      }}
                    >
                      <td>{when(r.when)}</td>
                      <td>{r.what || "—"}</td>
                      <td>{String(r.raw.body ?? "")}</td>
                      <td>
                        <Badge tone={r.passed ? "ready" : "attention"}>
                          {String(r.raw.status ?? "unknown")}
                        </Badge>{" "}
                        {r.result}
                      </td>
                      <td>{r.time || "—"}</td>
                    </tr>,
                    open === id && (
                      <tr key={id + ":detail"} className="detail-row">
                        <td colSpan={5}>
                          <Detail path={r.path} />
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > shown && (
          <Button variant="secondary" onClick={() => setShown(shown + PAGE)}>
            Show more ({rows.length - shown} left)
          </Button>
        )}
      </Card>
      <Card className="panel" title="House builds" description="Each build run with how long it took and how many blocks went down.">
        {builds === null ? (
          <p>Loading…</p>
        ) : builds.length === 0 ? (
          <div className="empty">No builds yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>House</th>
                  <th>Seed</th>
                  <th>Result</th>
                  <th>Time</th>
                  <th>Blocks placed</th>
                </tr>
              </thead>
              <tbody>
                {builds.slice(0, PAGE).map((b) => {
                  const r = buildRow(b);
                  return (
                    <tr key={r.run}>
                      <td>{when(r.when)}</td>
                      <td>{r.house}</td>
                      <td>{String(b.seed ?? "—")}</td>
                      <td>{r.result}</td>
                      <td>{r.time || "—"}</td>
                      <td>{r.placed || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
