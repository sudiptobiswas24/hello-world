import { useState } from "react";

import { useReference } from "../api/hooks";
import { aboveZero } from "../lib/decimal";
import { ActionButton } from "./Document";

interface Warehouse {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  is_quarantine: boolean;
  is_transit: boolean;
  consignment_vendor: number | null;
  held_for: number | null;
  receives_by_default?: boolean;
}

/** A warehouse goods may be shipped from, or a receipt defaults to: not quarantine, transit or someone else's stock. */
export function usableWarehouse(w: Record<string, unknown>): boolean {
  return w.is_active !== false && !w.is_quarantine && !w.is_transit && w.consignment_vendor == null && w.held_for == null;
}

/**
 * A warehouse a receipt may be put into (O612): quarantine too, where goods wait for quality (a subcontract made from
 * held batches must land there), and the vendor's own consignment store when the vendor is known. Never a
 * customer's material or another vendor's consignment store, which posting refuses; nor transit, where a transfer
 * rests on its way and a purchase has no business.
 */
export function receivableWarehouse(w: Record<string, unknown>, vendor?: number | null): boolean {
  if (w.is_active === false || w.is_transit || w.held_for != null) return false;
  return w.consignment_vendor == null || (vendor != null && w.consignment_vendor === vendor);
}

/**
 * Where a line naming no warehouse is received and planned (O415): the one marked to receive by default, or the
 * only usable warehouse. Null where neither, and the person chooses.
 */
export function defaultReceiving(rows: Record<string, unknown>[]): number | null {
  const marked = rows.find((row) => row.receives_by_default && row.is_active !== false);
  if (marked) return marked.id as number;
  const usable = rows.filter(usableWarehouse);
  return usable.length === 1 ? (usable[0]!.id as number) : null;
}

/**
 * Some line still owed names no warehouse of its own, so a delivery or a receipt drafted for the order must be
 * told one: the server refuses it otherwise ("Say which warehouse ..."). One rule for every screen that offers
 * Ship or Receive (O608).
 */
export function owesWithoutAWarehouse(lines: { charge?: number | null; quantity_open?: string | null; warehouse?: unknown }[]): boolean {
  return lines.some((line) => !line.charge && aboveZero(line.quantity_open) && !line.warehouse);
}

/**
 * Ship or Receive, asking where only when it has to: a line that names
 * its warehouse moves there, and where only one warehouse can take it,
 * that one is meant. A delivery ships from the company's own stock, so
 * quarantine, transit, consignment and a customer's material are not
 * offered. A receipt is offered what it may be put into
 * (receivableWarehouse): quarantine and its vendor's consignment store
 * too (O612). The one meant without asking is always the company's own.
 */
export function WarehouseChoice({ label, prompt, confirm, needsOne, pending, go, receivingFrom }: {
  label: string; // "Ship"
  prompt: string; // "Ship from"
  confirm: string; // "Draft delivery"
  /** Set for a receipt: the vendor, or null where it is not known, whose consignment store is then not offered. */
  receivingFrom?: number | null;
  /**
   * Some line still owed names no warehouse of its own. A screen that has not read the order's lines (the Scan
   * screen's offers) passes a function asking when pressed; null from it means stop, the screen has said why.
   */
  needsOne: boolean | (() => Promise<boolean | null>);
  pending: boolean;
  go: (warehouse: number | null) => void;
}) {
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const [asking, setAsking] = useState(false);
  const [checking, setChecking] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const rows = (warehouses.data ?? []) as unknown as Record<string, unknown>[];
  // Gone to without asking: only the company's own stock. Offered when asking: what a receipt may take, for one.
  const usable = rows.filter(usableWarehouse) as unknown as Warehouse[];
  const offered = (receivingFrom === undefined ? usable
    : rows.filter((w) => receivableWarehouse(w, receivingFrom))) as unknown as Warehouse[];

  const start = async () => {
    let needed: boolean | null = needsOne === true;
    if (typeof needsOne === "function") {
      setChecking(true);
      try {
        needed = await needsOne();
      } finally {
        setChecking(false);
      }
    }
    if (needed === null) return;
    if (!needed) go(null);
    else if (usable.length === 1) go(usable[0]!.id);
    else setAsking(true);
  };

  if (!asking) {
    const waiting = pending || checking || (needsOne !== false && warehouses.isPending);
    return <ActionButton primary pending={waiting} onClick={() => void start()}>{label}</ActionButton>;
  }
  return (
    <span className="ship-from">
      <label>
        <span>{prompt}</span>
        <select autoFocus value={chosen ?? ""} onChange={(e) => setChosen(Number(e.target.value) || null)}>
          <option value="">Choose a warehouse…</option>
          {offered.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
        </select>
      </label>
      <ActionButton primary pending={pending} disabled={chosen === null} onClick={() => go(chosen)}>{confirm}</ActionButton>
      <button type="button" className="btn btn-quiet" onClick={() => setAsking(false)}>Back</button>
    </span>
  );
}
