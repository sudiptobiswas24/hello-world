import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError, get } from "../../api/client";
import { useAct, useGet } from "../../api/hooks";
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

function Offer({ row, start }: { row: Match; start: (row: Match) => void }) {
  return (
    <li>
      <span>{row.label}</span>{row.state ? <span className="muted"> ({row.state})</span> : null}{" "}
      {row.actions.map((action) => {
        const screen = SCREEN[`${row.kind}:${action}`];
        return screen
          ? <Link key={action} className="btn" to={`/stores/${screen}/${row.id}`}>{VERB[action] ?? action}</Link>
          : <button key={action} type="button" className="btn" onClick={() => start(row)}>{VERB[action] ?? action}</button>;
      })}
    </li>
  );
}

export default function Scan() {
  const navigate = useNavigate();
  const drafts = useGet<Match[]>("/api/inventory/scan/open/");
  const act = useAct<{ id: number }>();
  const [found, setFound] = useState<Match[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

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
  // A confirmed purchase order: a draft receipt of what is still to come, then its scan screen.
  const start = async (row: Match) => {
    if (row.kind !== "purchase_order") return;
    const outcome = await act.run("POST", `/api/purchasing/purchase-orders/${row.id}/receive/`, {}, { done: `Receipt drafted for ${row.code}` });
    if (outcome.ok) navigate(`/stores/scan-receive/${outcome.data.id}`);
  };

  return (
    <article className="doc scan-screen">
      <header className="list-head"><h1>Scan</h1></header>
      <section className="sheet">
        <ScanBox label="Code" hint="Scan a label or a document, or type its code" onScan={scanned} busy={act.pending} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
        {found && (
          <ul className="scan-lines" aria-label="What it is">
            {found.map((row) => <Offer key={`${row.kind}-${row.id}`} row={row} start={(each) => void start(each)} />)}
          </ul>
        )}
      </section>
      <section className="sheet" aria-label="Open to scan into">
        <h2>Open to scan into</h2>
        {drafts.data && drafts.data.length === 0 && <p className="muted">Nothing open that you may scan into.</p>}
        <ul className="scan-lines">
          {(drafts.data ?? []).map((row) => <Offer key={`${row.kind}-${row.id}`} row={row} start={(each) => void start(each)} />)}
        </ul>
      </section>
    </article>
  );
}
