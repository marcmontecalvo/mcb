import { useEffect, useRef, useState } from "react";
import { Badge } from "@homenet/ui";
import {
  formatElapsed,
  isStalled,
  progressPercent,
  sessionName,
  toEpochSeconds,
} from "./model";

type Rec = Record<string, unknown>;

export function LiveSession({ session }: { session: Rec }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const logRef = useRef<HTMLPreElement>(null);
  const state = String(session.status || session.state || "unknown");
  const running = state === "running" || state === "queued";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const log = String(session.log || "");
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  const jobs = (session.jobs as Rec[] | undefined) || [];
  const job =
    jobs.find((j) => j.status === "running") ||
    [...jobs].reverse().find((j) => j.status !== "queued") ||
    jobs[0] ||
    {};
  const live = (job.live as Rec | undefined) || {};
  const progress = live.progress as Rec | undefined;
  const events = ((live.events as Rec[]) || []).slice().reverse();
  const started = toEpochSeconds(session.started_at ?? job.started_at);
  const finished = toEpochSeconds(session.finished_at);
  const elapsed = started ? (running ? now : (finished ?? now)) - started : null;
  const done = Number(progress?.done ?? 0);
  const total = Number(progress?.total ?? 0);
  const pct = progressPercent(done, total);
  const updatedAt = Number(progress?.updated_at) || undefined;
  const stalled = running && isStalled(updatedAt, now);
  const tone = running ? "working" : "quiet";
  const label = String(job.label || job.kind || session.label || session.id);

  return (
    <section className="live" aria-labelledby={`live-${session.id}`}>
      <h3 id={`live-${session.id}`}>{label}</h3>
      <div className="live-head" role="status" aria-live="polite">
        <Badge tone={tone}>{state}</Badge>
        {elapsed !== null && <span>Elapsed {formatElapsed(elapsed)}</span>}
        {progress && (
          <span>
            Stage <strong>{String(progress.stage ?? "working")}</strong>
            {total ? ` · ${done}/${total}` : ""}
          </span>
        )}
        {stalled && updatedAt && (
          <Badge tone="quiet">
            Stalled? no progress for {Math.round(now - updatedAt)}s
          </Badge>
        )}
      </div>
      {pct !== null && (
        <progress
          className="live-bar"
          value={done}
          max={total}
          aria-label={`Progress ${pct}%`}
        />
      )}
      {typeof live.explain === "string" && (
        <p className="live-explain">{live.explain}</p>
      )}
      {events.length > 0 && (
        <>
          <h4>Recent actions</h4>
          <table className="live-table">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Result</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i}>
                  <td>{String(e.op)}</td>
                  <td>{e.target ? String(e.target) : "-"}</td>
                  <td>{e.result === "done" ? "ok" : String(e.result)}</td>
                  <td>{e.reason ? String(e.reason) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <h4 id={`log-${session.id}`}>Log</h4>
      <pre
        ref={logRef}
        className="live-log"
        tabIndex={0}
        aria-labelledby={`log-${session.id}`}
      >
        {log || "No log output yet."}
      </pre>
      <details>
        <summary>Raw</summary>
        <pre>{JSON.stringify(session, null, 2)}</pre>
      </details>
    </section>
  );
}

export function RecentRun({ session }: { session: Rec }) {
  const [open, setOpen] = useState(false);
  const end = toEpochSeconds(session.finished_at);
  const start = toEpochSeconds(session.started_at);
  const when = end ?? start;
  return (
    <li className="recent-run">
      <details onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>
          <strong>{sessionName(session)}</strong>
          <Badge tone="quiet">{String(session.status || session.state || "unknown")}</Badge>
          {when && <span>{new Date(when * 1000).toLocaleString()}</span>}
          {start && end && <span>took {formatElapsed(end - start)}</span>}
        </summary>
        {open && <LiveSession session={session} />}
      </details>
    </li>
  );
}
