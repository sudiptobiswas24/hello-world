import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError, get } from "../../api/client";
import { useAct, useGet, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { owesWithoutAWarehouse, WarehouseChoice } from "../../forms/WarehouseChoice";
import { ScanBox } from "../../views/ScanBox";

/**
 * Scanning in stores (L9): scan anything and be told what it is, through
 * the one resolver (GET /api/inventory/scan/), with what this person may do
 * with it; and the drafts open to scan into. A draft has no number until it
 * posts, so its scan screen is opened from here or from the draft itself.
 */

interface Match { kind: string; id: number; code: string; label: string; state?: string; actions: string[] }

const SCREEN: Record<string, string> = {
  "goods_receipt:receive": "scan-receive",
  "delivery:pick": "scan-pick",
  "material_issue:issue": "scan-issue",
  "stock_transfer:transfer": "scan-transfer",
  "stock_count:count": "scan-count",
};
const VERB: Record<string, string> = { receive: "Receive", pick: "Pick", issue: "Issue", transfer: "Transfer", count: "Count" };

interface Owed { charge?: number | null; quantity_open?: string | null; warehouse?: number | null }

function Offer({ row, receive }: { row: Match; receive: Receive }) {
  return (
    <li>
      <span>{row.label}</span>{row.state ? <span className="muted"> ({row.state})</span> : null}{" "}
      {row.actions.map((action) => {
        const screen = SCREEN[`${row.kind}:${action}`];
        if (screen) return <Link key={action} className="btn" to={`/stores/${screen}/${row.id}`}>{VERB[action] ?? action}</Link>;
        // A confirmed purchase order: asks where it arrives only when a line still owed names nowhere (O608).
        if (row.kind === "purchase_order" && action === "receive") {
          return <WarehouseChoice key={action} label="Receive" prompt="Arrives at" confirm="Draft receipt"
            pending={receive.pending} needsOne={() => receive.owes(row)} go={(warehouse) => void receive.draft(row, warehouse)} />;
        }
        return null;
      })}
    </li>
  );
}

interface Receive {
  pending: boolean;
  owes: (row: Match) => Promise<boolean | null>;
  draft: (row: Match, warehouse: number | null) => Promise<void>;
}

export default function Scan() {
  const navigate = useNavigate();
  const drafts = useGet<Match[]>("/api/inventory/scan/open/");
  const act = useAct<{ id: number }>();
  const [found, setFound] = useState<Match[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const { can } = useAccess();
  const bins = can("inventory.view_storagebin") && can("inventory.view_warehouse");
  const warehouses = useReference<{ id: number; code: string; name: string }>("/api/inventory/warehouses/", undefined, bins);
  const [warehouse, setWarehouse] = useState("");
  const labelsOf = warehouse || String(warehouses.data?.[0]?.id ?? "");

  const scanned = async (code: string) => {
    setProblem(null);
    setFound(null);
    try {
      const answer = await get<{ matches: Match[] }>("/api/inventory/scan/", { code });
      setFound(answer.matches);
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : `${code} could not be looked up.`);
    }
  };
  // A confirmed purchase order: a draft receipt of what is still to come, then its scan screen. Where it arrives
  // is asked only when a line still owed names no warehouse, read from the order when Receive is pressed: the
  // offers are up to 25 orders, and reading each on sight would cost a request apiece.
  const receive: Receive = {
    pending: act.pending,
    owes: async (row) => {
      setProblem(null);
      try {
        const order = await get<{ lines: Owed[] }>(`/api/purchasing/purchase-orders/${row.id}/`);
        return owesWithoutAWarehouse(order.lines);
      } catch (error) {
        setProblem(error instanceof ApiError ? error.messages.join(" ") : `${row.code} could not be read.`);
        return null;
      }
    },
    draft: async (row, warehouse) => {
      const outcome = await act.run("POST", `/api/purchasing/purchase-orders/${row.id}/receive/`,
        warehouse ? { warehouse } : {}, { done: `Receipt drafted for ${row.code}` });
      if (outcome.ok) navigate(`/stores/scan-receive/${outcome.data.id}`);
    },
  };

  return (
    <article className="doc scan-screen">
      <header className="list-head"><h1>Scan</h1></header>
      <section className="sheet">
        <ScanBox label="Code" hint="Scan a label or a document, or type its code" onScan={scanned} busy={act.pending} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
        {found && (
          <ul className="scan-lines" aria-label="What it is">
            {found.map((row) => <Offer key={`${row.kind}-${row.id}`} row={row} receive={receive} />)}
          </ul>
        )}
      </section>
      {bins && (
        <section className="sheet" aria-label="Bin labels">
          <h2>Bin labels</h2>
          <label className="field">Warehouse{" "}
            <select aria-label="Warehouse" value={labelsOf} onChange={(event) => setWarehouse(event.target.value)}>
              {(warehouses.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.name || row.code}</option>)}
            </select>
          </label>{" "}
          {labelsOf && <a className="btn" href={`/api/inventory/bins/labels/?warehouse=${labelsOf}`} target="_blank" rel="noopener">Print bin labels</a>}
        </section>
      )}
      <section className="sheet" aria-label="Open to scan into">
        <h2>Open to scan into</h2>
        {drafts.data && drafts.data.length === 0 && <p className="muted">Nothing open that you may scan into.</p>}
        <ul className="scan-lines">
          {(drafts.data ?? []).map((row) => <Offer key={`${row.kind}-${row.id}`} row={row} receive={receive} />)}
        </ul>
      </section>
    </article>
  );
}
