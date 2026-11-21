import { useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, list } from "../../api/client";
import { useAct, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { defaultReceiving } from "../../forms/WarehouseChoice";
import { ScanBox } from "../../views/ScanBox";

interface Order { id: number; number: string; vendor_name?: string; status: string }
interface Warehouse { id: number; code: string; name: string; [field: string]: unknown }

/**
 * At the gate: scan the purchase order's number off the paper (every
 * printed order carries it as a barcode) and a draft receipt of what is
 * still to come opens, ready for the quantities that actually arrived.
 */
export default function ReceiveByScan() {
  const navigate = useNavigate();
  const act = useAct<{ id: number }>();
  const [problem, setProblem] = useState<string | null>(null);
  const { can } = useAccess();
  const seesWarehouses = can("inventory.view_warehouse");
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/", undefined, seesWarehouses);
  const [warehouse, setWarehouse] = useState("");
  // Where it arrives unless the person says otherwise: the warehouse marked to receive by default, else the only one
  // receiving to stock, as a line naming none is planned (O608's mirror). Not the first in the list: a quarantine
  // bay coded before the main store took every receipt scanned at the gate.
  const arrivesAt = warehouse || String(defaultReceiving(warehouses.data ?? []) ?? "");
  const scanned = async (code: string) => {
    setProblem(null);
    try {
      const found = await list<Order>("/api/purchasing/purchase-orders/", { search: code, page_size: 50 });
      const order = found.rows.find((row) => row.number.toLowerCase() === code.toLowerCase());
      if (!order) {
        setProblem(`No purchase order is numbered ${code}.`);
        return;
      }
      if (order.status !== "confirmed") {
        setProblem(`${order.number} is ${order.status}; only a confirmed order is received.`);
        return;
      }
      // The receipt goes where "Arrives at" says, and until the list has come it says nowhere. A hand scanner
      // types the moment the screen opens: wait for the list (the request already asked), or the scan is
      // sent naming no warehouse and refused for a line that names none either.
      const shown = warehouses.data
        ?? (seesWarehouses ? (await warehouses.refetch({ cancelRefetch: false })).data : undefined);
      const where = warehouse || String(defaultReceiving(shown ?? []) ?? "");
      const outcome = await act.run("POST", `/api/purchasing/purchase-orders/${order.id}/receive/`,
        where ? { warehouse: where } : {}, { done: `Receipt drafted for ${order.number}` });
      if (outcome.ok) navigate(`/purchasing/goods-in/${outcome.data.id}`);
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : `${code} could not be looked up.`);
    }
  };
  return (
    <article className="doc">
      <header className="list-head"><h1>Receive by scan</h1></header>
      <section className="sheet">
        <p className="muted">Scan the barcode on the purchase order the lorry brought, or type its number. A draft receipt of what is still to come opens.</p>
        <label className="field">Arrives at{" "}
          <select aria-label="Arrives at" value={arrivesAt} onChange={(event) => setWarehouse(event.target.value)}>
            {!arrivesAt && <option value="">Choose a warehouse…</option>}
            {(warehouses.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name || row.code}</option>)}
          </select>
        </label>
        <ScanBox label="Purchase order" onScan={scanned} busy={act.pending} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
      </section>
    </article>
  );
}
