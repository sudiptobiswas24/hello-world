import type { Query } from "../api/client";
import { useGet } from "../api/hooks";
import { csvText, downloadCsv } from "../lib/csv";
import { count, money, quantity } from "../lib/format";

interface Grouping { key: string; label: string }
interface SumDef { key: string; label: string; kind: "money" | "quantity" }
interface Group { key: string; label: string; count: number; sums: Record<string, string> }
interface Summary { by: Grouping[]; grouped_by?: string; sums?: SumDef[]; rows?: Group[] }

const SUMMARY = "/api/web/summary/";
const MONTH = ":month";

/**
 * The narrowing that shows one group's rows, or null where none can: a
 * month is its first to its last day; anything else is what the list
 * narrows by, at that value. A group of rows with nothing set opens nothing.
 */
function drill(by: string, key: string): Record<string, string> | null {
  if (!key) return null;
  if (by.endsWith(MONTH)) {
    const [year, month] = key.split("-").map(Number);
    if (!year || !month) return null;
    const last = new Date(year, month, 0).getDate();
    return { from: `${key}-01`, to: `${key}-${String(last).padStart(2, "0")}` };
  }
  return { [by]: key };
}

/**
 * The list as it stands, grouped: pick what to group by (what the list
 * narrows by, or its month) and see each group's count and the sum of
 * every figure the rows carry. The server reads with the list's own
 * permission and narrowing; this only shows.
 */
export function Grouped({ title, endpoint, narrowing, by, onBy, onDrill }: {
  title: string;
  endpoint: string;
  narrowing: Query;
  /** What it is grouped by, kept in the list's address so a refresh or a link keeps it. */
  by: string;
  onBy: (key: string) => void;
  /** Open one group's rows: the list narrowed to it. */
  onDrill: (changes: Record<string, string>) => void;
}) {
  const shape = useGet<Summary>(SUMMARY, { endpoint });
  const grouped = useGet<Summary>(SUMMARY, { ...narrowing, endpoint, by }, Boolean(by));
  const options = shape.data?.by ?? [];
  const rows = grouped.data?.rows ?? [];
  const sums = grouped.data?.sums ?? [];
  const cell = (sum: SumDef, value: string) => (sum.kind === "money" ? money(value) : quantity(value));
  const exportCsv = () => {
    const label = options.find((option) => option.key === by)?.label ?? by;
    downloadCsv(`${title} by ${label}`, csvText(
      [{ key: "label", label }, { key: "count", label: "Count" }, ...sums.map((sum) => ({ key: sum.key, label: sum.label }))],
      rows.map((row) => ({ label: row.label, count: row.count, ...row.sums })),
    ));
  };
  return (
    <section className="grouped" aria-label={`${title}, grouped`}>
      <div className="row-actions">
        <label>
          Group by{" "}
          <select aria-label="Group by" value={by} onChange={(event) => onBy(event.target.value)}>
            <option value="">—</option>
            {options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </label>
        {rows.length > 0 && <button type="button" className="btn" onClick={exportCsv}>CSV</button>}
        {grouped.error && <p className="form-error" role="alert">{grouped.error.messages.join(" ")}</p>}
      </div>
      {by && rows.length > 0 && (
        <table className="data">
          <thead>
            <tr>
              <th>{options.find((option) => option.key === by)?.label ?? by}</th>
              <th className="num">Count</th>
              {sums.map((sum) => <th key={sum.key} className="num">{sum.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key || "—"}>
                <td>{drill(by, row.key)
                  ? <button type="button" className="link" onClick={() => onDrill(drill(by, row.key)!)}>{row.label}</button>
                  : row.label}</td>
                <td className="num">{count(row.count)}</td>
                {sums.map((sum) => <td key={sum.key} className="num">{cell(sum, row.sums[sum.key] ?? "")}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {by && grouped.data && rows.length === 0 && <p className="muted">Nothing to group.</p>}
    </section>
  );
}
