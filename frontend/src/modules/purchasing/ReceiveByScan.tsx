import { useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, list } from "../../api/client";
import { useAct, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ScanBox } from "../../views/ScanBox";

interface Order { id: number; number: string; vendor_name?: string; status: string }
interface Warehouse { id: number; code: string; name: string }

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
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/", undefined, can("inventory.view_warehouse"));
  const [warehouse, setWarehouse] = useState("");
  const arrivesAt = warehouse || String(warehouses.data?.[0]?.id ?? "");
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
      const outcome = await act.run("POST", `/api/purchasing/purchase-orders/${order.id}/receive/`,
        arrivesAt ? { warehouse: arrivesAt } : {}, { done: `Receipt drafted for ${order.number}` });
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
            {(warehouses.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name || row.code}</option>)}
          </select>
        </label>
        <ScanBox label="Purchase order" onScan={scanned} busy={act.pending} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
      </section>
    </article>
  );
}
