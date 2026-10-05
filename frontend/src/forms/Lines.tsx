import { useState } from "react";

import { useAct, useReference } from "../api/hooks";
import { aboveZero } from "../lib/decimal";
import { money, quantity } from "../lib/format";
import { CommitDecimal, DecimalInput } from "./fields";
import { RecordPicker } from "./RecordPicker";

export interface TradeLine {
  id: number;
  item: number | null;
  description: string;
  /** Its description, else its charge or item: what the server calls it. */
  label?: string;
  quantity: string;
  unit_price: string | null;
  discount_percent: string;
  taxes: number[];
  net_amount: string;
  total: string;
  charge?: number | null;
  [extra: string]: unknown;
}

interface Item {
  id: number;
  sku: string;
  name: string;
  uom: number;
  sale_price: string | null;
}

interface Tax {
  id: number;
  code: string;
  name: string;
  rate: string;
  scope: string;
  is_active: boolean;
}

export interface ExtraColumn {
  label: string;
  render: (line: TradeLine) => string;
}

/**
 * The lines of an order, a quotation or an invoice: the same arithmetic
 * on all three, so one editor. While the document is a draft a line is
 * changed where it stands, saved as the box is left, and the totals come
 * back from the server; nothing here does the sums.
 *
 * A new line needs only an item and a quantity: the server resolves the
 * price from the price list, as it would for a line typed in elsewhere.
 */
export function Lines({ lines, endpoint, parent, parentId, editable, extra = [], withUom, side = "sales", closable }: {
  lines: TradeLine[];
  endpoint: string; // /api/sales/sales-order-lines/
  parent: string; // "order", "quotation", "invoice"
  parentId: number;
  editable: boolean;
  extra?: ExtraColumn[];
  withUom?: boolean;
  /** Which taxes apply: a sale's, or a purchase's. */
  side?: "sales" | "purchase";
  /** A confirmed order's lines may be closed short (and reopened) by whoever may change it. */
  closable?: boolean;
}) {
  const act = useAct();
  const taxes = useReference<Tax>("/api/accounting/taxes/");
  const offered = (taxes.data ?? []).filter((tax) => tax.is_active && (tax.scope === "both" || tax.scope === side));
  const taxCode = (id: number) => taxes.data?.find((tax) => tax.id === id)?.code ?? `#${id}`;

  const [item, setItem] = useState<Item | null>(null);
  const [qty, setQty] = useState("1");
  // Left empty, the server prices it: the price list, or the vendor's
  // agreed price. Typed, it is the price, which is how a purchase with no
  // agreed price is bought at all.
  const [price, setPrice] = useState("");

  const patch = (line: TradeLine, change: Partial<TradeLine>) => {
    const changed = Object.entries(change).some(([key, value]) => JSON.stringify(line[key]) !== JSON.stringify(value));
    if (changed) void act.run("PATCH", `${endpoint}${line.id}/`, change);
  };

  const add = async () => {
    if (!item || !qty || Number.isNaN(Number(qty))) return;
    const body: Record<string, unknown> = { [parent]: parentId, item: item.id, quantity: qty };
    if (withUom) body.uom = item.uom;
    if (price) body.unit_price = price;
    const outcome = await act.run("POST", endpoint, body, { done: `${item.name} added` });
    if (outcome.ok) {
      setItem(null);
      setQty("1");
      setPrice("");
    }
  };

  return (
    <div className="lines">
      <table>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col" className="k-quantity">Quantity</th>
            <th scope="col" className="k-money">Unit price</th>
            <th scope="col" className="k-quantity">Disc. %</th>
            <th scope="col">Taxes</th>
            {extra.map((column) => <th key={column.label} scope="col" className="k-quantity">{column.label}</th>)}
            <th scope="col" className="k-money">Amount</th>
            {editable && <th scope="col" aria-label="Remove" />}
            {closable && <th scope="col">Rest</th>}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.id}>
              <td className="line-what">{line.label || line.description || "—"}</td>
              <td className="k-quantity">
                {editable ? (
                  <CommitDecimal value={line.quantity} label="Quantity" onCommit={(v) => patch(line, { quantity: v })} />
                ) : quantity(line.quantity)}
              </td>
              <td className="k-money">
                {editable ? (
                  <CommitDecimal value={line.unit_price ?? ""} places={2} label="Unit price" onCommit={(v) => patch(line, { unit_price: v })} />
                ) : money(line.unit_price)}
              </td>
              <td className="k-quantity">
                {editable ? (
                  <CommitDecimal value={line.discount_percent} places={2} label="Discount percent" onCommit={(v) => patch(line, { discount_percent: v || "0" })} />
                ) : quantity(line.discount_percent)}
              </td>
              <td>
                {editable ? (
                  <select
                    multiple
                    className="tax-select"
                    aria-label="Taxes"
                    value={line.taxes.map(String)}
                    onChange={(event) => patch(line, { taxes: Array.from(event.target.selectedOptions, (o) => Number(o.value)) })}
                  >
                    {offered.map((tax) => <option key={tax.id} value={tax.id}>{tax.code}</option>)}
                  </select>
                ) : line.taxes.map(taxCode).join(", ")}
              </td>
              {extra.map((column) => <td key={column.label} className="k-quantity">{column.render(line)}</td>)}
              <td className="k-money">{money(line.net_amount)}</td>
              {editable && (
                <td>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Remove ${line.label || line.description}`}
                    disabled={act.pending}
                    onClick={() => {
                      if (window.confirm(`Remove ${line.label || line.description || "this line"}?`)) {
                        void act.run("DELETE", `${endpoint}${line.id}/`, undefined, { done: "Line removed" });
                      }
                    }}
                  >
                    ×
                  </button>
                </td>
              )}
              {closable && (
                <td><CloseShort line={line} endpoint={endpoint} side={side} /></td>
              )}
            </tr>
          ))}
          {lines.length === 0 && (
            <tr><td colSpan={7 + extra.length + (closable ? 1 : 0)} className="empty-line">No lines yet.</td></tr>
          )}
        </tbody>
      </table>
      {editable && (
        <form
          className="add-line"
          onSubmit={(event) => {
            event.preventDefault();
            void add();
          }}
        >
          <RecordPicker<Item>
            endpoint="/api/inventory/items/"
            value={item?.id ?? null}
            onChange={(_, row) => setItem(row)}
            label={(row) => `${row.sku} · ${row.name}`}
            detail={(row) => (row.sale_price ? `List ${money(row.sale_price)}` : "")}
            fixed={{ is_active: "true" }}
            placeholder="Add an item: type its code or name"
            ariaLabel="Item to add"
          />
          <DecimalInput value={qty} onChange={setQty} aria-label="Quantity to add" className="qty" />
          <DecimalInput value={price} onChange={setPrice} places={2} aria-label="Unit price to add" className="qty"
            placeholder={side === "purchase" ? "Agreed price" : "List price"} />
          <button type="submit" className="btn" disabled={!item || act.pending}>Add line</button>
        </form>
      )}
    </div>
  );
}

/**
 * The rest of a line that will never come or go: closed short with a
 * reason, or reopened. The reason is asked for in the row, not in a
 * dialog, and the server refuses what it must (billed beyond what moved).
 */
function CloseShort({ line, endpoint, side }: { line: TradeLine; endpoint: string; side: "sales" | "purchase" }) {
  const act = useAct();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const name = line.label || line.description || "this line";
  if (line.closed_short_at) {
    return (
      <span className="closed-short">
        <small title={line.closed_short_reason as string}>Closed short</small>{" "}
        <button type="button" className="btn small" disabled={act.pending}
          onClick={() => void act.run("POST", `${endpoint}${line.id}/reopen/`, {}, { done: `${name} reopened` })}>
          Reopen
        </button>
      </span>
    );
  }
  if (line.charge || !aboveZero(line.quantity_open as string)) return null;
  if (!asking) {
    return (
      <button type="button" className="btn small" aria-label={`Close ${name} short`} onClick={() => setAsking(true)}>
        Close short
      </button>
    );
  }
  return (
    <form
      className="close-short"
      onSubmit={async (event) => {
        event.preventDefault();
        const outcome = await act.run("POST", `${endpoint}${line.id}/close-short/`, { reason }, { done: `${name} closed short` });
        if (outcome.ok) {
          setAsking(false);
          setReason("");
        }
      }}
    >
      <input
        aria-label={`Why the rest of ${name} will not ${side === "purchase" ? "come" : "ship"}`}
        placeholder={side === "purchase" ? "Why it will not come" : "Why it will not ship"}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        autoFocus
      />
      <button type="submit" className="btn small" disabled={!reason.trim() || act.pending}>Close</button>
      <button type="button" className="btn small ghost" onClick={() => setAsking(false)}>Cancel</button>
    </form>
  );
}
