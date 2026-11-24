// "Who's here": the people online on the arena server and the saved names, one
// click each, so nobody retypes a Minecraft name. Lives inside the roster field of
// the arena wizard (RosterInput in fields.tsx).
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { humanize } from "./forms";
import type { Book, Choice, Online } from "./roster";

const MAX_LABEL = 32; // mclab.arena_roster.MAX_LABEL

/** The saved roster and who is online, loaded when the wizard opens; `refresh` asks
 * the arena server again. Trouble is a message, never a thrown error: the wizard
 * works without either list. */
export function useRosterBook() {
  const [book, setBook] = useState<Book | null>(null);
  const [online, setOnline] = useState<Online | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const answer = await api<Online>("/api/roster/online");
      if (alive.current) setOnline(answer);
    } catch (e) {
      if (alive.current)
        setOnline({
          online: [],
          bot: "",
          note: `Couldn't ask the dashboard who is online: ${(e as Error).message}`,
        });
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    api<Book>("/api/roster").then(
      (b) => alive.current && setBook(b),
      (e) => alive.current && setProblem(`Couldn't load the saved names: ${e.message}`),
    );
    void refresh();
  }, [refresh]);
  const change = async (path: string, body: Record<string, unknown>) => {
    try {
      const next = await api<Book>(path, "POST", body);
      if (alive.current) {
        setBook(next);
        setProblem("");
      }
    } catch (e) {
      if (alive.current) setProblem((e as Error).message);
    }
  };
  return {
    book,
    online,
    loading,
    problem,
    refresh,
    /** Save a name, or change its label or pin. */
    save: (name: string, fields: { label?: string; pinned?: boolean } = {}) =>
      change("/api/roster", { name, ...fields }),
    forget: (name: string) => change("/api/roster/remove", { name }),
  };
}

type RowProps = {
  choice: Choice;
  teams: string[];
  full: boolean;
  onAdd: (name: string, extra: { team?: string; op: boolean }) => void;
  onSave: (name: string, fields: { label?: string; pinned?: boolean }) => void;
  onForget: (name: string) => void;
};

function ChoiceRow({ choice, teams, full, onAdd, onSave, onForget }: RowProps) {
  const [op, setOp] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(choice.label);
  const settled = useRef(true); // a label edit ends once (Enter, Escape or blur)
  const finish = (keep: boolean) => {
    if (settled.current) return;
    settled.current = true;
    setEditing(false);
    const label = draft.trim();
    if (keep && label !== choice.label) onSave(choice.name, { label });
  };
  return (
    <li className="choice">
      <span
        className={`presence${choice.online ? " on" : ""}`}
        title={choice.online ? "Online now" : "Not online"}
        role="img"
        aria-label={choice.online ? "Online now" : "Not online"}
      />
      <span className="who">
        {editing ? (
          <input
            autoFocus
            aria-label={`Label for ${choice.name}`}
            placeholder="Label, e.g. Dad"
            maxLength={MAX_LABEL}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => finish(true)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                finish(true);
              } else if (e.key === "Escape") finish(false);
            }}
          />
        ) : (
          <>
            <strong>{choice.label || choice.name}</strong>
            {choice.label && <small>{choice.name}</small>}
          </>
        )}
      </span>
      <label className="op" title="Make them an operator on the arena server">
        <input
          type="checkbox"
          aria-label={`${choice.name} will be an operator`}
          checked={op}
          onChange={(e) => setOp(e.target.checked)}
        />
        Op
      </label>
      <span className="adds">
        {teams.length ? (
          teams.map((team) => (
            <button
              type="button"
              key={team}
              className="add-to"
              disabled={full}
              aria-label={`Add ${choice.name} to ${humanize(team)}`}
              onClick={() => onAdd(choice.name, { team, op })}
            >
              {humanize(team)}
            </button>
          ))
        ) : (
          <button
            type="button"
            className="add-to"
            disabled={full}
            aria-label={`Add ${choice.name}`}
            onClick={() => onAdd(choice.name, { op })}
          >
            Add
          </button>
        )}
      </span>
      <span className="tools">
        <button
          type="button"
          className="link"
          aria-pressed={choice.pinned}
          aria-label={`${choice.pinned ? "Unpin" : "Pin"} ${choice.name}`}
          title={choice.pinned ? "Unpin" : "Pin to the top"}
          onClick={() => onSave(choice.name, { pinned: !choice.pinned })}
        >
          {choice.pinned ? "★" : "☆"}
        </button>
        <button
          type="button"
          className="link"
          aria-label={`Label ${choice.name}`}
          title="Give them a label, like Dad or Son"
          onClick={() => {
            settled.current = false;
            setDraft(choice.label);
            setEditing(true);
          }}
        >
          ✎
        </button>
        {choice.saved && (
          <button
            type="button"
            className="link"
            aria-label={`Forget ${choice.name}`}
            title="Remove from the saved names"
            onClick={() => onForget(choice.name)}
          >
            ✕
          </button>
        )}
      </span>
    </li>
  );
}

export function RosterPicker({
  choices,
  anyone,
  teams,
  full,
  loading,
  note,
  problem,
  onRefresh,
  onAdd,
  onSave,
  onForget,
}: {
  choices: Choice[];
  /** Whether anyone is online or saved at all (they may all be in the match). */
  anyone: boolean;
  /** The teams a person can be added to now (none in a game against the waves). */
  teams: string[];
  full: boolean;
  loading: boolean;
  note: string | null;
  problem: string;
  onRefresh: () => void;
  onAdd: RowProps["onAdd"];
  onSave: RowProps["onSave"];
  onForget: RowProps["onForget"];
}) {
  return (
    <div className="roster-picker">
      <div className="picker-head">
        <strong>Who's here</strong>
        <button
          type="button"
          className="link"
          disabled={loading}
          aria-label="Refresh who is online"
          onClick={onRefresh}
        >
          {loading ? "Looking…" : "↻ Refresh"}
        </button>
      </div>
      {note && <small className="warn">{note}</small>}
      {problem && <span className="field-error">{problem}</span>}
      {choices.length ? (
        <ul className="choices">
          {choices.map((choice) => (
            <ChoiceRow
              key={choice.name.toLowerCase()}
              choice={choice}
              teams={teams}
              full={full}
              onAdd={onAdd}
              onSave={onSave}
              onForget={onForget}
            />
          ))}
        </ul>
      ) : (
        !loading && (
          <small>
            {anyone
              ? "Everyone listed here is already in the match."
              : "Nobody else is online and no names are saved yet. Type a name below; every name in a match you start is saved here."}
          </small>
        )
      )}
    </div>
  );
}
