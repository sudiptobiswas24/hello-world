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

/** A warehouse goods may be received into or shipped from: not quarantine, transit or someone else's stock. */
export function usableWarehouse(w: Record<string, unknown>): boolean {
  return w.is_active !== false && !w.is_quarantine && !w.is_transit && w.consignment_vendor == null && w.held_for == null;
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
 * that one is meant. Quarantine, transit, consignment and a customer's
 * own material are never a delivery's or a receipt's to choose; the
 * server refuses them at posting, so they are not offered.
 */
export function WarehouseChoice({ label, prompt, confirm, needsOne, pending, go }: {
  label: string; // "Ship"
  prompt: string; // "Ship from"
  confirm: string; // "Draft delivery"
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
  const usable = (warehouses.data ?? []).filter((w) =>
    w.is_active && !w.is_quarantine && !w.is_transit && w.consignment_vendor === null && w.held_for === null);

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
          {usable.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
        </select>
      </label>
      <ActionButton primary pending={pending} disabled={chosen === null} onClick={() => go(chosen)}>{confirm}</ActionButton>
      <button type="button" className="btn btn-quiet" onClick={() => setAsking(false)}>Back</button>
    </span>
  );
}
