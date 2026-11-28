import { useState } from "react";
import { Link } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { RecordPicker } from "../../forms/RecordPicker";
import { date } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { PORT } from "./ports";

interface Lacking {
  invoice: number;
  number: string;
  invoice_date: string;
  customer_name: string;
  export: boolean;
  why: string;
}

const ENDPOINT = "/api/gst/eway-bills/";

/**
 * One posted invoice of goods that says nowhere they went, and the box to
 * say it: the state they were delivered in or, for an export, the place in
 * India they left the plant for. It is recorded on the invoice with who
 * said so and when; nothing the invoice billed changes.
 */
function Lacks({ row, may }: { row: Lacking; may: boolean }) {
  const act = useAct();
  const [state, setState] = useState("");
  const [port, setPort] = useState<number | null>(null);
  const save = () => void act.run("POST", `${ENDPOINT}record-where-it-went/`,
    row.export ? { invoice: row.invoice, port_address: port } : { invoice: row.invoice, state },
    { done: `Recorded on ${row.number}` });
  return (
    <section className="related-list" aria-label={row.number}>
      <h2><Link to={`/sales/invoices/${row.invoice}`}>{row.number}</Link> · {row.customer_name} · {date(row.invoice_date)}</h2>
      <p className="muted">{row.why}</p>
      {may && (
        <div className="field-grid">
          {row.export ? (
            <Field label="Left the plant for" hint="The port, ICD or CFS in India">
              {(fid) => <RecordPicker<Record<string, unknown> & { id: number }> id={fid} endpoint={PORT.endpoint} value={port}
                onChange={(next) => setPort(next as number | null)} label={PORT.label} fixed={PORT.query} ariaLabel={`Port for ${row.number}`} />}
            </Field>
          ) : (
            <Field label="State delivered in" hint="Its name, or its two-digit GST code">
              {(fid) => <input id={fid} value={state} onChange={(e) => setState(e.target.value)} aria-label={`State for ${row.number}`} />}
            </Field>
          )}
        </div>
      )}
      {may && <ActionButton primary pending={act.pending} disabled={row.export ? port === null : !state.trim()} onClick={save}>Record where it went</ActionButton>}
    </section>
  );
}

/** Posted invoices no e-way bill can be made for until where their goods went is recorded. */
export default function WhereItWent() {
  const { can } = useAccess();
  const found = useGet<Lacking[]>(`${ENDPOINT}lacking/`);
  if (found.isError) return <ErrorPanel error={found.error} retry={() => void found.refetch()} />;
  if (!found.data) return <div className="loading">Opening…</div>;
  return (
    <article className="doc">
      <h1>Invoices that say nowhere their goods went</h1>
      <p className="muted">
        A posted invoice is not edited. Where its goods were delivered is recorded on it here, with who recorded it and when,
        and its e-way bill and e-invoice read that.
      </p>
      {found.data.length === 0 && <p role="status">Every posted invoice of goods says where they went.</p>}
      {found.data.map((row) => <Lacks key={row.invoice} row={row} may={can("gst.add_ewaybill")} />)}
    </article>
  );
}
