import { useEffect, useState } from "react";

import { useAct, useReference } from "../api/hooks";
import { TaxPicker } from "./TaxPicker";
import { aboveZero } from "../lib/decimal";
import { money, quantity } from "../lib/format";
import { CommitDecimal, DecimalInput } from "./fields";
import { RecordPicker } from "./RecordPicker";
import { defaultReceiving, usableWarehouse } from "./WarehouseChoice";
import { useAccess } from "../auth/me";

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

interface Centre { id: number; code: string; name: string }

/**
 * The lines of an order, a quotation or an invoice: the same arithmetic
 * on all three, so one editor. While the document is a draft a line is
 * changed where it stands, saved as the box is left, and the totals come
 * back from the server; nothing here does the sums.
 *
 * A new line needs only an item and a quantity: the server resolves the
 * price from the price list, as it would for a line typed in elsewhere.
 */
interface Place { id: number; code: string; name: string; [field: string]: unknown }

export function Lines({ lines, endpoint, parent, parentId, editable, extra = [], withUom, side = "sales", closable, centres, receiving }: {
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
  /** Purchase lines name the cost centre their expense is for (accounting/analytic.py). */
  centres?: boolean;
  /**
   * A purchase line is asked where it is received, defaulted to the default receiving warehouse: one naming none
   * is planned there only, and a planner in another warehouse was told to cancel what the plant needed (O415).
   */
  receiving?: boolean;
}) {
  const act = useAct();
  const taxes = useReference<Tax>("/api/accounting/taxes/");
  const offered = (taxes.data ?? []).filter((tax) => tax.is_active && (tax.scope === "both" || tax.scope === side));
  // While the taxes load a line says so, not a row id (#1, #2) nobody can read (O394).
  const taxCode = (id: number) => (taxes.data ? taxes.data.find((tax) => tax.id === id)?.code ?? `#${id}` : "…");

  const patch = (line: TradeLine, change: Partial<TradeLine>) => {
    const changed = Object.entries(change).some(([key, value]) => JSON.stringify(line[key]) !== JSON.stringify(value));
    if (changed) void act.run("PATCH", `${endpoint}${line.id}/`, change);
  };

  const add = async (line: NewLine) => {
    const outcome = await act.run("POST", endpoint, { [parent]: parentId, ...lineBody(line) }, { done: `${line.label} added` });
    return outcome.ok;
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
            {centres && <th scope="col">Cost centre</th>}
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
                  <TaxPicker chosen={line.taxes} offered={taxes.data ? offered : undefined} label="Taxes"
                    onChange={(next) => patch(line, { taxes: next })} />
                ) : line.taxes.length ? line.taxes.map(taxCode).join(", ") : <span className="tax-chosen none">No tax</span>}
              </td>
              {extra.map((column) => <td key={column.label} className="k-quantity">{column.render(line)}</td>)}
              {centres && (
                <td>
                  {editable ? (
                    <RecordPicker<Centre> endpoint="/api/accounting/cost-centres/" value={(line.cost_centre as number | null) ?? null}
                      onChange={(next) => patch(line, { cost_centre: next })} label={(row) => `${row.code} · ${row.name}`}
                      fixed={{ is_active: "true" }} placeholder="Centre" ariaLabel={`Cost centre of ${line.label || line.description}`} />
                  ) : String(line.cost_centre_name ?? "") || "—"}
                </td>
              )}
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
            <tr><td colSpan={7 + extra.length + (closable ? 1 : 0) + (centres ? 1 : 0)} className="empty-line">No lines yet.</td></tr>
          )}
        </tbody>
      </table>
      {editable && <AddLine side={side} receiving={receiving} withUom={withUom} pending={act.pending} onAdd={add} />}
    </div>
  );
}

/** A line of a document not yet saved: sent with its header in one request (O401). */
export interface NewLine {
  key: number;
  item: number;
  label: string;
  quantity: string;
  /** Empty: the server prices it, as it would a line added later. */
  unit_price: string;
  uom?: number;
  warehouse?: number | null;
}

/** What a line sends: no taxes named, so it takes its item's (O459), as one added later does. */
export function lineBody(line: NewLine): Record<string, unknown> {
  const body: Record<string, unknown> = { item: line.item, quantity: line.quantity };
  if (line.uom !== undefined) body.uom = line.uom;
  if (line.unit_price) body.unit_price = line.unit_price;
  if (line.warehouse !== undefined && line.warehouse !== null) body.warehouse = line.warehouse;
  return body;
}

let nextKey = 1;

/**
 * The add-a-line row, the same on a saved document and a new one: an item, a quantity, and a price left empty for
 * the server to find. `onAdd` says whether it was taken, and the row is cleared only then.
 */
function AddLine({ side, receiving, withUom, pending, onAdd }: {
  side: "sales" | "purchase";
  receiving?: boolean;
  withUom?: boolean;
  pending?: boolean;
  onAdd: (line: NewLine) => Promise<boolean> | boolean;
}) {
  const [item, setItem] = useState<Item | null>(null);
  const [qty, setQty] = useState("1");
  // Left empty, the server prices it: the price list, or the vendor's
  // agreed price. Typed, it is the price, which is how a purchase with no
  // agreed price is bought at all.
  const [price, setPrice] = useState("");
  const { can } = useAccess();
  const places = useReference<Place>("/api/inventory/warehouses/", undefined,
    Boolean(receiving && can("inventory.view_warehouse")));
  const usable = (places.data ?? []).filter(usableWarehouse);
  const [place, setPlace] = useState<number | null>(null);
  useEffect(() => {
    if (place === null && places.data) setPlace(defaultReceiving(places.data));
  }, [places.data, place]);
  const add = async () => {
    if (!item || !qty || Number.isNaN(Number(qty))) return;
    const line: NewLine = { key: nextKey++, item: item.id, label: `${item.sku} · ${item.name}`, quantity: qty, unit_price: price };
    if (withUom) line.uom = item.uom;
    if (receiving && place !== null) line.warehouse = place;
    if (await onAdd(line)) {
      setItem(null);
      setQty("1");
      setPrice("");
    }
  };
  return (
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
      {receiving && places.data && (
        <select aria-label="Received at" value={place ?? ""} onChange={(e) => setPlace(Number(e.target.value) || null)}>
          <option value="">Received at…</option>
          {usable.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
        </select>
      )}
      <button type="submit" className="btn" disabled={!item || pending}>Add line</button>
    </form>
  );
}

/**
 * The lines of a document not yet saved (O401). Kept on the screen and sent with the header in one request, which
 * makes both or neither: a person who stops half-way leaves nothing behind. Prices left empty and taxes are worked
 * out by the server as the document is made, so the amounts show once it is.
 */
export function NewLines({ lines, onChange, errors, side = "sales", receiving, withUom }: {
  lines: NewLine[];
  onChange: (next: NewLine[]) => void;
  /** What the server said, by path: new_lines[1].quantity. */
  errors?: Record<string, string[]>;
  side?: "sales" | "purchase";
  receiving?: boolean;
  withUom?: boolean;
}) {
  const said = (index: number) => Object.entries(errors ?? {})
    .filter(([path]) => path.startsWith(`new_lines[${index}]`))
    .map(([path, messages]) => `${path.slice(`new_lines[${index}]`.length + 1) || "Line"}: ${messages.join(" ")}`);
  return (
    <div className="lines">
      <table aria-label="Lines">
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col" className="k-quantity">Quantity</th>
            <th scope="col" className="k-money">Unit price</th>
            <th scope="col" aria-label="Remove" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={line.key}>
              <td className="line-what">
                {line.label}
                {said(index).map((message) => <div key={message} className="field-error" role="alert">{message}</div>)}
              </td>
              <td className="k-quantity">{quantity(line.quantity)}</td>
              <td className="k-money">{line.unit_price ? money(line.unit_price) : <span className="muted">{side === "purchase" ? "Agreed price" : "List price"}</span>}</td>
              <td>
                <button type="button" className="icon-btn" aria-label={`Remove ${line.label}`}
                  onClick={() => onChange(lines.filter((other) => other.key !== line.key))}>×</button>
              </td>
            </tr>
          ))}
          {lines.length === 0 && <tr><td colSpan={4} className="empty-line">No lines yet.</td></tr>}
        </tbody>
      </table>
      {errors?.new_lines && <p className="form-error" role="alert">{errors.new_lines.join(" ")}</p>}
      <AddLine side={side} receiving={receiving} withUom={withUom} onAdd={(line) => { onChange([...lines, line]); return true; }} />
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
