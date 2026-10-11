import { useMemo } from "react";
import { useSearchParams } from "react-router";

import { useGet, useReference } from "../../api/hooks";
import { count, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Row {
  item: string;
  item_id: number;
  item_name: string;
  uom: string;
  warehouse: string;
  warehouse_id: number;
  warehouse_name: string;
  quantity: string;
  unit_cost: string;
  value: string;
}

interface Valuation {
  as_of: string | null;
  total_value: string;
  rows: Row[];
}

interface Warehouse {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
}

/**
 * What is on every shelf and what it is worth, as the stock ledger
 * replays it: nothing here is a stored count. One warehouse or all; a
 * search narrows what is shown without asking the server again.
 */
export default function StockOnHand() {
  const [params, setParams] = useSearchParams();
  const warehouse = params.get("warehouse") ?? "";
  const search = (params.get("q") ?? "").trim().toLowerCase();
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const report = useGet<Valuation>("/api/inventory/stock-reports/valuation/", warehouse ? { warehouse } : undefined);

  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });

  const rows = useMemo(() => (report.data?.rows ?? []).filter((row) =>
    !search || `${row.item} ${row.item_name}`.toLowerCase().includes(search)), [report.data, search]);

  return (
    <section className="report">
      <header className="list-head">
        <h1>Stock on hand</h1>
        <label className="inline">
          Where
          <select value={warehouse} onChange={(e) => set("warehouse", e.target.value)}>
            <option value="">Every warehouse</option>
            {(warehouses.data ?? []).filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
          </select>
        </label>
        <div className="search">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
          <input type="search" placeholder="Item code or name" aria-label="Find an item"
            value={params.get("q") ?? ""} onChange={(e) => set("q", e.target.value)} />
        </div>
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <>
          <div className="tiles">
            <div className="tile"><span>Worth</span><strong>{report.data ? money(report.data.total_value) : "…"}</strong></div>
            <div className="tile"><span>Lines held</span><strong>{report.data ? count(report.data.rows.length) : "…"}</strong></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Item</th><th scope="col">Where</th>
                  <th scope="col" className="k-quantity">On hand</th><th scope="col" className="k-money">Unit cost</th>
                  <th scope="col" className="k-money">Worth</th>
                </tr>
              </thead>
              <tbody>
                {report.isPending ? <tr className="skeleton"><td colSpan={5}><span /></td></tr> : rows.map((row) => (
                  <tr key={`${row.item_id}-${row.warehouse_id}`}>
                    <td><strong>{row.item}</strong> {row.item_name}</td>
                    <td>{row.warehouse}</td>
                    <td className="k-quantity">{quantity(row.quantity)} <small className="muted">{row.uom}</small></td>
                    <td className="k-money">{money(row.unit_cost, 4)}</td>
                    <td className="k-money">{money(row.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.data && rows.length === 0 && <div className="empty"><p>{search ? "No item held matches that." : "Nothing on hand here."}</p></div>}
          </div>
        </>
      )}
    </section>
  );
}
