import { useState } from "react";

import { useGet } from "../../api/hooks";
import { RecordPicker } from "../../forms/RecordPicker";
import { date } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Status { lot: string; item: string; status: string; inspection: string | null; inspected_on: string | null; result: string | null; disposition: string | null }
type Lot = { id: number; code: string; item_label?: string };

/** The picker's and the planner's question: may this batch go, and what said so? */
export default function BatchStatus() {
  const [lot, setLot] = useState<number | null>(null);
  const status = useGet<Status>(`/api/quality/lot-status/${lot}/`, undefined, lot !== null);
  return (
    <section className="report">
      <header className="list-head">
        <h1>Batch status</h1>
        <label className="inline">Batch{" "}
          <RecordPicker<Lot> endpoint="/api/inventory/lots/" value={lot} onChange={(id) => setLot(id)}
            label={(row) => `${row.code} · ${row.item_label ?? ""}`} ariaLabel="Batch" />
        </label>
      </header>
      {lot === null ? <div className="empty"><p>Choose a batch.</p></div>
        : status.isError ? <ErrorPanel error={status.error} retry={() => void status.refetch()} />
        : status.data ? (
          <dl className="totals">
            <div className="strong"><dt>{status.data.lot} · {status.data.item}</dt><dd>{status.data.status}</dd></div>
            <div><dt>Last inspection</dt><dd>{status.data.inspection ?? "None"}</dd></div>
            <div><dt>On</dt><dd>{date(status.data.inspected_on) || "—"}</dd></div>
            <div><dt>Result</dt><dd>{status.data.result ?? "—"}</dd></div>
            <div><dt>Decided</dt><dd>{status.data.disposition ?? "—"}</dd></div>
          </dl>
        ) : <div className="loading">Reading…</div>}
    </section>
  );
}
