import { useState } from "react";
import { useSearchParams } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { today } from "../../forms/fields";
import { RecordPicker } from "../../forms/RecordPicker";
import { date, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { DataTable } from "../../views/DataTable";

interface Order { id: number; number: string; customer_name: string }
interface Account { id: number; code: string; name: string }
type Line = { delivery: string; order_line: number; quantity: string; index_value: string; variation_per_unit: string; amount: string };
type Bill = {
  id: number; number: string; order: string; start: string; end: string; total: string;
  invoice: number | null; credit_note: number | null; cancelled: boolean; lines: Line[];
};

const LINE_COLUMNS = [
  { key: "delivery", label: "Delivery", width: "10rem" },
  { key: "quantity", label: "Quantity", width: "9rem", render: (row: Line) => quantity(row.quantity) },
  { key: "index_value", label: "Index then", width: "9rem", render: (row: Line) => quantity(row.index_value) },
  { key: "variation_per_unit", label: "Change a unit", width: "9rem", render: (row: Line) => money(row.variation_per_unit) },
  { key: "amount", label: "Amount", width: "10rem", render: (row: Line) => money(row.amount) },
];

/**
 * What an order's index clauses owe for the deliveries in a span, shown
 * before anything is written; billed, an invoice (or a credit note, when
 * polymer fell) for the difference. A bill is cancelled while its
 * documents are drafts.
 */
export default function VariationBills() {
  const { can } = useAccess();
  const [params, setParams] = useSearchParams();
  const order = params.get("order");
  const start = params.get("start") ?? `${today().slice(0, 8)}01`;
  const end = params.get("end") ?? today();
  const [account, setAccount] = useState<number | null>(null);
  const ready = Boolean(order);
  const preview = useGet<Line[]>("/api/sales/price-variation-bills/preview/", { order: order ?? "", start, end }, ready);
  const bills = useGet<Bill[]>("/api/sales/price-variation-bills/");
  const act = useAct<Bill>();
  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });
  return (
    <section className="report">
      <header className="list-head">
        <h1>Price variation bills</h1>
        <label className="inline">Order{" "}
          <RecordPicker<Order> endpoint="/api/sales/sales-orders/" fixed={{ status: "confirmed" }} value={order ? Number(order) : null}
            onChange={(id) => set("order", id ? String(id) : "")} label={(row) => `${row.number} · ${row.customer_name}`} ariaLabel="Order" />
        </label>
        <label className="inline">From <input type="date" value={start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="inline">To <input type="date" value={end} onChange={(e) => set("end", e.target.value)} /></label>
      </header>
      {ready && (preview.isError ? <ErrorPanel error={preview.error} retry={() => void preview.refetch()} />
        : !preview.data ? <div className="loading">Working it out…</div>
        : (
          <>
            <DataTable rows={preview.data} columns={LINE_COLUMNS} empty="No deliveries in these days moved with the index." />
            {preview.data.length > 0 && can("sales.add_pricevariationbill") && (
              <div className="row-actions">
                <label className="inline">Receivable account{" "}
                  <RecordPicker<Account> endpoint="/api/accounting/accounts/" value={account} onChange={(id) => setAccount(id)}
                    label={(row) => `${row.code} · ${row.name}`} ariaLabel="Receivable account" />
                </label>
                <ActionButton primary pending={act.pending} onClick={() => {
                  if (account === null) return;
                  void act.run("POST", "/api/sales/price-variation-bills/", { order, start, end, receivable_account: account },
                    { done: (bill) => `${bill.number} written` });
                }}>Bill the variation</ActionButton>
              </div>
            )}
          </>
        ))}
      <h2 className="section-title">Bills written</h2>
      {bills.isError ? <ErrorPanel error={bills.error} retry={() => void bills.refetch()} />
        : <DataTable<Bill> rows={bills.data ?? []} pending={bills.isPending} empty="None yet." columns={[
            { key: "number", label: "Bill", width: "10rem" },
            { key: "order", label: "Order", width: "10rem" },
            { key: "start", label: "Deliveries", render: (row) => `${date(row.start)} to ${date(row.end)}` },
            { key: "total", label: "Total", width: "10rem", render: (row) => money(row.total) },
            { key: "cancelled", label: "", width: "9rem", render: (row) => row.cancelled ? "Cancelled" : can("sales.change_pricevariationbill") ? (
              <button type="button" className="btn" disabled={act.pending} onClick={() => {
                if (!window.confirm(`Cancel ${row.number}? Its draft invoice or credit note goes with it.`)) return;
                void act.run("POST", `/api/sales/price-variation-bills/${row.id}/cancel/`, {}, { done: "Cancelled" });
              }}>Cancel</button>) : "" },
          ]} />}
    </section>
  );
}
