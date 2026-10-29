import { useGet } from "../api/hooks";
import { dateTime } from "../lib/format";

export interface Change { field: string; label: string; from: unknown; to: unknown; secret?: boolean }
interface HistoryRow { id: number; at: string; who: string; through: string; kind: string; label: string; summary: string; changes?: Change[] }

/** A value as the log kept it: exact text, as it came. */
function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "nothing";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Each field a change touched, with what it was and what it became; a secret only as changed. */
export function ChangeList({ changes }: { changes?: Change[] }) {
  if (!changes?.length) return null;
  return (
    <ul className="changes">
      {changes.map((change) => (
        <li key={change.field}>
          {change.label}: {change.secret ? "changed" : `${shown(change.from)} → ${shown(change.to)}`}
        </li>
      ))}
    </ul>
  );
}

/**
 * What happened to a record, by whom and through what: made, each field
 * changed with its old and new value, each action (posted, voided, sent
 * to whom), read under the record's own view permission. Nothing to show
 * is nothing shown.
 */
export function HistoryPanel({ model, id }: { model: string; id: number }) {
  const rows = useGet<HistoryRow[]>("/api/core/history/", { model, id: String(id) });
  if (!rows.data?.length) return null;
  return (
    <section className="related-list chatter" aria-label="History">
      <h2>History</h2>
      <table className="data">
        <tbody>
          {rows.data.map((row) => (
            <tr key={row.id}>
              <td className="muted">{dateTime(row.at)}</td>
              <td>{row.who || "nobody signed in"}{row.through && row.through !== "api" ? ` (${row.through})` : ""}</td>
              <td>
                {row.label}{row.kind === "action" || row.kind === "mail" ? (row.summary ? ` — ${row.summary}` : "") : ""}
                <ChangeList changes={row.changes} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
