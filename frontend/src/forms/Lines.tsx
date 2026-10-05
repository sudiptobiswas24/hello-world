import { useState } from "react";

import { useAct, useReference } from "../api/hooks";
import { money, quantity } from "../lib/format";
import { DecimalInput } from "./fields";
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
export function Lines({ lines, endpoint, parent, parentId, editable, extra = [], withUom }: {
  lines: TradeLine[];
  endpoint: string; // /api/sales/sales-order-lines/
  parent: string; // "order", "quotation", "invoice"
  parentId: number;
  editable: boolean;
  extra?: ExtraColumn[];
  withUom?: boolean;
}) {
  const act = useAct();
  const taxes = useReference<Tax>("/api/accounting/taxes/");
  const saleTaxes = (taxes.data ?? []).filter((tax) => tax.is_active && tax.scope !== "purchase");
  const taxCode = (id: number) => taxes.data?.find((tax) => tax.id === id)?.code ?? `#${id}`;

  const [item, setItem] = useState<Item | null>(null);
  const [qty, setQty] = useState("1");

  const patch = (line: TradeLine, change: Partial<TradeLine>) => {
    const changed = Object.entries(change).some(([key, value]) => JSON.stringify(line[key]) !== JSON.stringify(value));
    if (changed) void act.run("PATCH", `${endpoint}${line.id}/`, change);
  };

  const add = async () => {
    if (!item || !qty || Number.isNaN(Number(qty))) return;
    const body: Record<string, unknown> = { [parent]: parentId, item: item.id, quantity: qty };
    if (withUom) body.uom = item.uom;
    const outcome = await act.run("POST", endpoint, body, { done: `${item.name} added` });
    if (outcome.ok) {
      setItem(null);
      setQty("1");
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
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.id}>
              <td className="line-what">{line.label || line.description || "—"}</td>
              <td className="k-quantity">
                {editable ? (
                  <LazyDecimal value={line.quantity} label="Quantity" onCommit={(v) => patch(line, { quantity: v })} />
                ) : quantity(line.quantity)}
              </td>
              <td className="k-money">
                {editable ? (
                  <LazyDecimal value={line.unit_price ?? ""} places={2} label="Unit price" onCommit={(v) => patch(line, { unit_price: v })} />
                ) : money(line.unit_price)}
              </td>
              <td className="k-quantity">
                {editable ? (
                  <LazyDecimal value={line.discount_percent} places={2} label="Discount percent" onCommit={(v) => patch(line, { discount_percent: v || "0" })} />
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
                    {saleTaxes.map((tax) => <option key={tax.id} value={tax.id}>{tax.code}</option>)}
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
            </tr>
          ))}
          {lines.length === 0 && (
            <tr><td colSpan={7 + extra.length} className="empty-line">No lines yet.</td></tr>
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
          <button type="submit" className="btn" disabled={!item || act.pending}>Add line</button>
        </form>
      )}
    </div>
  );
}

/** A figure edited in place and saved when the box is left, if it changed. */
function LazyDecimal({ value, onCommit, places = 4, label }: { value: string; onCommit: (value: string) => void; places?: number; label: string }) {
  const [typed, setTyped] = useState<string | null>(null);
  return (
    <DecimalInput
      className="cell-input"
      aria-label={label}
      places={places}
      value={typed ?? trim(value)}
      onChange={setTyped}
      onBlur={() => {
        if (typed !== null && typed !== trim(value)) onCommit(typed);
        setTyped(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") setTyped(null);
      }}
    />
  );
}

function trim(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}
