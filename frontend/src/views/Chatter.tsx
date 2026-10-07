import { useState } from "react";
import { useLocation } from "react-router";

import { useAct, useGet } from "../api/hooks";
import { useAccess } from "../auth/me";
import { today } from "../forms/fields";
import { date, dateTime } from "../lib/format";

interface NoteRow { id: number; body: string; by: string; at: string; mine: boolean }

export interface FollowUpRow {
  id: number; kind: string; kind_label: string; summary: string; note: string; due_on: string;
  assigned_to: number; assigned_to_name: string; planned_by: string; done_on: string | null; done_by: string;
  outcome: string; link: string; state: "planned" | "today" | "overdue" | "done"; may_change: boolean;
  record?: string; record_kind?: string;
}

interface Person { id: number; name: string }

const NOTES = "/api/core/notes/";
const FOLLOW_UPS = "/api/core/follow-ups/";
export const KINDS: [string, string][] = [
  ["call", "Call"], ["visit", "Visit or meeting"], ["email", "Email or message"], ["document", "Document to get"], ["todo", "To do"],
];
const STATE_WORDS = { planned: "", today: "Today", overdue: "Late", done: "Done" } as const;

function tomorrow(): string {
  const day = new Date(`${today()}T00:00:00`);
  day.setDate(day.getDate() + 1);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
}

/** What people have said about the record, newest first; written by whoever may read it. */
export function NotesPanel({ model, id }: { model: string; id: number }) {
  const { can } = useAccess();
  const rows = useGet<NoteRow[]>(NOTES, { model, id: String(id) });
  const act = useAct();
  const [body, setBody] = useState("");
  const mayAdd = can("core.add_note");
  if (!rows.data?.length && !mayAdd) return null;
  const write = async () => {
    const outcome = await act.run("POST", NOTES, { model, id, body }, { done: "Noted" });
    if (outcome.ok) setBody("");
  };
  return (
    <section className="related-list chatter" aria-label="Notes">
      <h2>Notes</h2>
      {mayAdd && (
        <form className="note-form" onSubmit={(event) => { event.preventDefault(); void write(); }}>
          <textarea aria-label="A note" rows={2} value={body} placeholder="What was said, what was agreed…"
            onChange={(event) => setBody(event.target.value)} />
          <button type="submit" className="btn" disabled={act.pending || !body.trim()}>Add note</button>
        </form>
      )}
      {rows.data?.length ? (
        <ul className="notes">
          {rows.data.map((row) => (
            <li key={row.id}>
              <div className="muted">{row.by} · {dateTime(row.at)}
                {row.mine && (
                  <button type="button" className="icon-btn" aria-label="Remove this note"
                    onClick={() => void act.run("DELETE", `${NOTES}${row.id}/`, undefined, { done: "Note removed" })}>×</button>
                )}
              </div>
              <p>{row.body}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** One follow-up: done with how it went, moved to another day, or removed by its planner. */
function FollowUpLine({ row }: { row: FollowUpRow }) {
  const act = useAct();
  const [outcome, setOutcome] = useState<string | null>(null);
  return (
    <tr className={row.state === "overdue" ? "bad" : undefined}>
      <td>{row.kind_label}</td>
      <td>{row.summary}{row.note ? <div className="muted">{row.note}</div> : null}
        {row.done_on && <div className="muted">Done by {row.done_by} on {date(row.done_on)}{row.outcome ? `: ${row.outcome}` : ""}</div>}
      </td>
      <td>{row.assigned_to_name}</td>
      <td>
        {row.may_change
          ? <input type="date" aria-label={`Due: ${row.summary}`} value={row.due_on}
              onChange={(event) => event.target.value && void act.run("PATCH", `${FOLLOW_UPS}${row.id}/`, { due_on: event.target.value }, { done: "Moved" })} />
          : date(row.due_on)}
        {STATE_WORDS[row.state] && <span className={`pill ${row.state === "overdue" ? "bad" : row.state === "done" ? "done" : "info"}`}>{STATE_WORDS[row.state]}</span>}
      </td>
      <td>
        {row.may_change && (outcome === null
          ? (
            <>
              <button type="button" className="btn" onClick={() => setOutcome("")}>Done</button>
              <button type="button" className="icon-btn" aria-label={`Remove: ${row.summary}`}
                onClick={() => void act.run("DELETE", `${FOLLOW_UPS}${row.id}/`, undefined, { done: "Removed" })}>×</button>
            </>
          )
          : (
            <form onSubmit={(event) => {
              event.preventDefault();
              void act.run("POST", `${FOLLOW_UPS}${row.id}/done/`, { outcome }, { done: "Done" }).then((done) => done.ok && setOutcome(null));
            }}>
              <input aria-label="How it went" value={outcome} placeholder="How it went" onChange={(event) => setOutcome(event.target.value)} />
              <button type="submit" className="btn primary" disabled={act.pending}>Mark done</button>
            </form>
          ))}
      </td>
    </tr>
  );
}

/** What is planned on the record, and for whom: a call to make, a document to get. */
export function FollowUpsPanel({ model, id }: { model: string; id: number }) {
  const { can } = useAccess();
  const location = useLocation();
  const mayAdd = can("core.add_followup");
  const rows = useGet<FollowUpRow[]>(FOLLOW_UPS, { model, id: String(id) });
  const people = useGet<Person[]>(`${FOLLOW_UPS}people/`, undefined, mayAdd);
  const act = useAct();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ kind: "call", summary: "", due_on: tomorrow(), assigned_to: "", note: "" });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  if (!rows.data?.length && !mayAdd) return null;
  const plan = async () => {
    const outcome = await act.run("POST", FOLLOW_UPS, {
      model, id, ...draft, assigned_to: draft.assigned_to ? Number(draft.assigned_to) : undefined, link: location.pathname,
    }, { done: "Planned" });
    if (outcome.ok) {
      setOpen(false);
      setErrors({});
      setDraft({ kind: "call", summary: "", due_on: tomorrow(), assigned_to: "", note: "" });
    } else setErrors(outcome.error.fields);
  };
  const set = (key: keyof typeof draft) => (event: { target: { value: string } }) => setDraft((now) => ({ ...now, [key]: event.target.value }));
  return (
    <section className="related-list chatter" aria-label="Follow-ups">
      <h2>Follow-ups</h2>
      {rows.data?.length ? (
        <table className="data">
          <tbody>{rows.data.map((row) => <FollowUpLine key={row.id} row={row} />)}</tbody>
        </table>
      ) : null}
      {mayAdd && !open && <button type="button" className="btn" onClick={() => setOpen(true)}>Plan a follow-up</button>}
      {mayAdd && open && (
        <form className="field-grid" onSubmit={(event) => { event.preventDefault(); void plan(); }}>
          <label className="field">What
            <select value={draft.kind} onChange={set("kind")}>{KINDS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          </label>
          <label className="field">About<input value={draft.summary} onChange={set("summary")} placeholder="Ask about the October schedule" />
            {errors.summary && <span className="field-error">{errors.summary.join(" ")}</span>}
          </label>
          <label className="field">By<input type="date" value={draft.due_on} onChange={set("due_on")} />
            {errors.due_on && <span className="field-error">{errors.due_on.join(" ")}</span>}
          </label>
          <label className="field">For
            <select value={draft.assigned_to} onChange={set("assigned_to")}>
              <option value="">Me</option>
              {(people.data ?? []).map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
            </select>
            {errors.assigned_to && <span className="field-error">{errors.assigned_to.join(" ")}</span>}
          </label>
          <label className="field wide">Note<input value={draft.note} onChange={set("note")} /></label>
          <div>
            <button type="submit" className="btn primary" disabled={act.pending}>Plan it</button>
            <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}
