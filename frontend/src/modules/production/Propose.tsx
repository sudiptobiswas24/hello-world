import { useSearchParams } from "react-router";

import { useAct, useGet, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { RecordPicker } from "../../forms/RecordPicker";
import { date, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { DataTable } from "../../views/DataTable";

interface Proposal {
  method: string; history_months: number; level: string; year_on_year_growth: string | null;
  backtest: Record<string, string | number | null>; notes: string[];
  rows: { starts_on: string; ends_on: string; quantity: string }[];
}
interface Warehouse { id: number; code: string; name: string }
type Item = { id: number; sku: string; name: string };

/**
 * What shipments by season say the coming months will ship, shown before
 * anything is kept: the method, how far back it looked, and how wrong it
 * would have been last year. Kept, the months become forecasts.
 */
export default function Propose() {
  const { can } = useAccess();
  const [params, setParams] = useSearchParams();
  const item = params.get("item");
  const warehouse = params.get("warehouse") ?? "";
  const months = params.get("months") ?? "6";
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const ready = Boolean(item && warehouse);
  const query = { item: item ?? "", warehouse, months };
  const proposal = useGet<Proposal>("/api/planning/forecasts/propose/", query, ready);
  const act = useAct();
  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });
  return (
    <section className="report">
      <header className="list-head">
        <h1>Forecast from history</h1>
        <label className="inline">Item{" "}
          <RecordPicker<Item> endpoint="/api/inventory/items/" value={item ? Number(item) : null}
            onChange={(id) => set("item", id ? String(id) : "")} label={(row) => `${row.sku} · ${row.name}`} ariaLabel="Item" />
        </label>
        <label className="inline">Warehouse <select value={warehouse} onChange={(e) => set("warehouse", e.target.value)}>
          <option value="">Choose…</option>
          {(warehouses.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name || row.code}</option>)}
        </select></label>
        <label className="inline">Months <select value={months} onChange={(e) => set("months", e.target.value)}>
          {["3", "6", "12"].map((v) => <option key={v} value={v}>{v}</option>)}
        </select></label>
        {ready && proposal.data && proposal.data.rows.length > 0 && can("planning.add_forecast") && (
          <ActionButton primary pending={act.pending} onClick={() => void act.run("POST", "/api/planning/forecasts/accept/",
            { item, warehouse, months }, { done: "Kept as forecasts" })}>Keep as forecasts</ActionButton>
        )}
      </header>
      {!ready ? <div className="empty"><p>Choose an item and a warehouse.</p></div>
        : proposal.isError ? <ErrorPanel error={proposal.error} retry={() => void proposal.refetch()} />
        : !proposal.data ? <div className="loading">Working it out…</div>
        : (
          <>
            <p className="note" role="note">
              {proposal.data.method}, from {proposal.data.history_months} months of shipments.
              {proposal.data.backtest.mape_percent != null
                ? ` Tried on last year, its months were out by ${proposal.data.backtest.mape_percent}% on average.`
                : proposal.data.backtest.note ? ` ${proposal.data.backtest.note}` : ""}
            </p>
            {proposal.data.notes.map((note) => <p key={note} className="muted">{note}</p>)}
            <DataTable rows={proposal.data.rows} empty="Not enough history to propose anything." columns={[
              { key: "starts_on", label: "From", render: (row) => date(row.starts_on) },
              { key: "ends_on", label: "To", render: (row) => date(row.ends_on) },
              { key: "quantity", label: "Expected", kind: "quantity", render: (row) => quantity(row.quantity) },
            ]} />
          </>
        )}
    </section>
  );
}
