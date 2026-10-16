import { useSearchParams } from "react-router";

import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { sum, toPaise } from "../../lib/decimal";
import { money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Row {
  key: string;
  net: string;
  tax: string;
  gross: string;
  quantity: string;
}

const GROUPS: [string, string][] = [["customer", "Customer"], ["item", "Item"], ["month", "Month"], ["rep", "Rep"], ["industry", "Industry"]];

/** The Indian financial year began on 1 April, in the plant's own date. */
function financialYearStart(): string {
  const [year, month] = today().split("-").map(Number) as [number, number];
  return `${month >= 4 ? year : year - 1}-04-01`;
}

/** Net sales, less credit notes, over a period, by customer, item or month. */
export default function Revenue() {
  const [params, setParams] = useSearchParams();
  const groupBy = params.get("group_by") ?? "customer";
  const from = params.get("from") ?? financialYearStart();
  const to = params.get("to") ?? "";
  const report = useGet<Row[]>("/api/sales/invoices/revenue/", { group_by: groupBy, from, to });

  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });

  const rows = report.data ?? [];
  const total = sum(rows.map((row) => row.net));
  const largest = rows.reduce((max, row) => (toPaise(row.net) > max ? toPaise(row.net) : max), 0n);

  return (
    <section className="report">
      <header className="list-head">
        <h1>Sales</h1>
        <div className="segmented" role="group" aria-label="Group by">
          {GROUPS.map(([key, label]) => (
            <button key={key} type="button" className={groupBy === key ? "on" : undefined} aria-pressed={groupBy === key} onClick={() => set("group_by", key)}>{label}</button>
          ))}
        </div>
        <label className="inline">From <input type="date" value={from} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="inline">To <input type="date" value={to} onChange={(e) => set("to", e.target.value)} /></label>
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{GROUPS.find(([key]) => key === groupBy)?.[1]}</th><th scope="col" aria-label="Share" /><th scope="col" className="k-quantity">Quantity</th><th scope="col" className="k-money">Net</th><th scope="col" className="k-money">Tax</th><th scope="col" className="k-money">Gross</th></tr></thead>
            <tbody>
              {report.isPending ? <tr className="skeleton"><td colSpan={6}><span /></td></tr> : rows.map((row) => {
                // The bar is drawing, not arithmetic: a float is fine here.
                const share = largest > 0n && toPaise(row.net) > 0n ? Number((toPaise(row.net) * 1000n) / largest) / 10 : 0;
                return (
                  <tr key={row.key}>
                    <td>{row.key}</td>
                    <td className="bar-cell"><span className="bar" style={{ width: `${share}%` }} /></td>
                    <td className="k-quantity">{quantity(row.quantity)}</td>
                    <td className="k-money">{money(row.net)}</td>
                    <td className="k-money">{money(row.tax)}</td>
                    <td className="k-money">{money(row.gross)}</td>
                  </tr>
                );
              })}
            </tbody>
            {rows.length > 0 && (
              <tfoot><tr><th scope="row">Total</th><td /><td /><td className="k-money">{money(total)}</td><td className="k-money">{money(sum(rows.map((r) => r.tax)))}</td><td className="k-money">{money(sum(rows.map((r) => r.gross)))}</td></tr></tfoot>
            )}
          </table>
          {report.data && rows.length === 0 && <div className="empty"><p>No sales in this period.</p></div>}
        </div>
      )}
    </section>
  );
}
