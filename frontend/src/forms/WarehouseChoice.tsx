import { useState } from "react";

import { useReference } from "../api/hooks";
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
  /** Some line still owed names no warehouse of its own. */
  needsOne: boolean;
  pending: boolean;
  go: (warehouse: number | null) => void;
}) {
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const [asking, setAsking] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const usable = (warehouses.data ?? []).filter((w) =>
    w.is_active && !w.is_quarantine && !w.is_transit && w.consignment_vendor === null && w.held_for === null);

  const start = () => {
    if (!needsOne) go(null);
    else if (usable.length === 1) go(usable[0]!.id);
    else setAsking(true);
  };

  if (!asking) return <ActionButton primary pending={pending || (needsOne && warehouses.isPending)} onClick={start}>{label}</ActionButton>;
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
