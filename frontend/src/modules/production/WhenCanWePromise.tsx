import { useSearchParams } from "react-router";

import { useGet, useReference } from "../../api/hooks";
import { DecimalInput } from "../../forms/fields";
import { RecordPicker } from "../../forms/RecordPicker";
import { date } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Answer { date: string | null; source: string; note: string; from_stock: unknown }
interface Warehouse { id: number; code: string; name: string }
type Item = { id: number; sku: string; name: string };

/**
 * What a rep can say on the telephone: when this much could be had, from
 * stock or from a run. It reserves nothing; confirming the order does.
 */
export default function WhenCanWePromise() {
  const [params, setParams] = useSearchParams();
  const item = params.get("item");
  const warehouse = params.get("warehouse") ?? "";
  const quantity = params.get("quantity") ?? "";
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const ready = Boolean(item && warehouse && quantity);
  const answer = useGet<Answer>("/api/planning/promise/when/", { item: item ?? "", warehouse, quantity }, ready);
  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });
  return (
    <section className="report">
      <header className="list-head">
        <h1>When can we promise?</h1>
        <label className="inline">Item{" "}
          <RecordPicker<Item> endpoint="/api/inventory/items/" value={item ? Number(item) : null}
            onChange={(id) => set("item", id ? String(id) : "")} label={(row) => `${row.sku} · ${row.name}`} ariaLabel="Item" />
        </label>
        <label className="inline">From <select value={warehouse} onChange={(e) => set("warehouse", e.target.value)}>
          <option value="">Choose…</option>
          {(warehouses.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name || row.code}</option>)}
        </select></label>
        <label className="inline">How many <DecimalInput aria-label="How many" value={quantity} onChange={(v) => set("quantity", v)} /></label>
      </header>
      {!ready ? <div className="empty"><p>Choose an item and a warehouse, and say how many.</p></div>
        : answer.isError ? <ErrorPanel error={answer.error} retry={() => void answer.refetch()} />
        : !answer.data ? <div className="loading">Working it out…</div>
        : (
          <dl className="totals">
            <div className="strong"><dt>Can be had by</dt><dd>{date(answer.data.date) || "Not within the horizon"}</dd></div>
            <div><dt>From</dt><dd>{answer.data.source}</dd></div>
            {answer.data.note && <div><dt>Note</dt><dd>{answer.data.note}</dd></div>}
          </dl>
        )}
    </section>
  );
}
