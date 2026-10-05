import { Link, useSearchParams } from "react-router";

import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { sum } from "../../lib/decimal";
import { count, date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Entry {
  id: number;
  number: string;
  customer: string;
  due_date: string;
  days_overdue: number;
  amount_due: string;
}
type Buckets = Record<string, { count: number; total: string; invoices: Entry[] }>;

const ORDER = ["current", "1-30", "31-60", "61-90", "90+"];
const LABEL: Record<string, string> = { current: "Not yet due", "1-30": "1–30 days late", "31-60": "31–60", "61-90": "61–90", "90+": "Over 90" };

/** What customers owe, by how late it is, as at a date. */
export default function Aging() {
  const [params, setParams] = useSearchParams();
  const asOf = params.get("as_of") ?? today();
  const shown = params.get("bucket");
  const report = useGet<Buckets>("/api/sales/invoices/aging/", { as_of: asOf });

  const set = (key: string, value: string | null) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });

  const data = report.data;
  const overdue = data ? sum(ORDER.slice(1).map((key) => data[key]?.total)) : "0";
  const owed = data ? sum(ORDER.map((key) => data[key]?.total)) : "0";
  const rows = data ? (shown ? data[shown]?.invoices ?? [] : ORDER.flatMap((key) => data[key]?.invoices ?? [])) : [];

  return (
    <section className="report">
      <header className="list-head">
        <h1>Receivables by age</h1>
        <label className="inline">As at <input type="date" value={asOf} onChange={(e) => set("as_of", e.target.value)} /></label>
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <>
          <div className="tiles">
            <div className="tile"><span>Owed in all</span><strong>{data ? money(owed) : "…"}</strong></div>
            <div className="tile bad"><span>Overdue</span><strong>{data ? money(overdue) : "…"}</strong></div>
            {ORDER.map((key) => (
              <button key={key} type="button" className={`tile pick${shown === key ? " on" : ""}`} aria-pressed={shown === key}
                onClick={() => set("bucket", shown === key ? null : key)}>
                <span>{LABEL[key]}</span>
                <strong>{data ? money(data[key]?.total) : "…"}</strong>
                <small>{data ? `${count(data[key]?.count ?? 0)} due` : ""}</small>
              </button>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Invoice</th><th scope="col">Customer</th><th scope="col">Due</th><th scope="col" className="k-quantity">Days late</th><th scope="col" className="k-money">Owed</th></tr></thead>
              <tbody>
                {report.isPending ? <tr className="skeleton"><td colSpan={5}><span /></td></tr> : rows.map((row, index) => (
                  <tr key={`${row.id}-${index}`}>
                    <td><Link to={`/sales/invoices/${row.id}`}>{row.number}</Link></td>
                    <td>{row.customer}</td>
                    <td>{date(row.due_date)}</td>
                    <td className="k-quantity">{row.days_overdue || ""}</td>
                    <td className="k-money">{money(row.amount_due)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && rows.length === 0 && <div className="empty"><p>Nothing owed{shown ? " in this band" : ""}.</p></div>}
          </div>
        </>
      )}
    </section>
  );
}
