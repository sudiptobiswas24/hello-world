import { useGet } from "../api/hooks";
import { dateTime } from "../lib/format";

interface HistoryRow { id: number; at: string; who: string; kind: string; label: string; summary: string }

/**
 * What happened to a record and by whom: made, which fields changed,
 * each action (posted, voided, sent to whom), read under the record's
 * own view permission. Nothing to show is nothing shown.
 */
export function HistoryPanel({ model, id }: { model: string; id: number }) {
  const rows = useGet<HistoryRow[]>("/api/core/history/", { model, id: String(id) });
  if (!rows.data?.length) return null;
  return (
    <section className="related" aria-label="History">
      <h2 className="section-title">History</h2>
      <table className="data">
        <tbody>
          {rows.data.map((row) => (
            <tr key={row.id}>
              <td className="muted">{dateTime(row.at)}</td>
              <td>{row.who}</td>
              <td>{row.label}{row.summary ? ` — ${row.summary}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
