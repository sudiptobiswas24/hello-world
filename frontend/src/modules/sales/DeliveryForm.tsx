import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ATTACHMENTS, type AttachmentRow, sizeOf, Trail } from "../../views/Trail";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, get, request } from "../../api/client";
import { useAct, useGet, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { ScanBox } from "../../views/ScanBox";
import { CommitDecimal, DecimalInput, Field } from "../../forms/fields";
import { aboveZero } from "../../lib/decimal";
import { date, dateTime, quantity } from "../../lib/format";
import { PartyPicker } from "../../forms/PartyPicker";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface DeliveryLine {
  id: number;
  order_line: number;
  description: string;
  warehouse: number;
  quantity_shipped: string;
  lot: number | null;
}

interface Delivery {
  id: number;
  number: string;
  sales_order: number;
  order_number: string;
  customer_name: string;
  delivery_date: string;
  reference: string;
  posted: boolean;
  reverses: number | null;
  backorder_of: number | null;
  lines: DeliveryLine[];
  transporter: number | null;
  transporter_name: string;
  lr_number: string;
  lr_date: string | null;
  vehicle_number: string;
  received_on: string | null;
  received_by: string;
  receipt_reference: string;
}

/** Bales scanned onto a draft delivery, one label at a time: the bale's bundles become its lines. */
function LoadBales({ delivery }: { delivery: Delivery }) {
  const act = useAct<unknown>();
  const [problem, setProblem] = useState<string | null>(null);
  const scanned = async (code: string) => {
    setProblem(null);
    try {
      const bale = await get<{ id: number; number: string }>("/api/manufacturing/bales/scan/", { number: code });
      await act.run("POST", "/api/manufacturing/bales/load/", { delivery: delivery.id, bales: [bale.id] },
        { done: `${bale.number} loaded` });
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : `${code} could not be loaded.`);
    }
  };
  return (
    <section className="related-list" aria-label="Load bales">
      <h2>Load bales</h2>
      <ScanBox label="Scan a bale label" onScan={scanned} busy={act.pending} />
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
}

interface PickRow { id: number; warehouse: string; bin: string; item: string; lot: string; quantity: string; for: string[]; problem: string }

/** The route through the shelves before anything moves: which bin, which batch, how much, in walking order. */
function PickList({ delivery }: { delivery: Delivery }) {
  const list = useGet<{ rows: PickRow[] }>(`${ENDPOINT}${delivery.id}/pick-list/`);
  return (
    <section className="related-list" aria-label="Pick list">
      <h2>Pick list</h2>
      {list.isError ? <p className="muted">Could not plan it: {list.error.message}</p>
        : !list.data ? <p className="muted">Planning…</p>
        : list.data.rows.length === 0 ? <p className="muted">Nothing to pick.</p> : (
        <table>
          <thead>
            <tr><th scope="col">Bin</th><th scope="col">Goods</th><th scope="col">Batch</th><th scope="col" className="k-quantity">Quantity</th><th scope="col">Problem</th></tr>
          </thead>
          <tbody>
            {list.data.rows.map((row) => (
              <tr key={row.id}>
                <td>{row.bin || "—"}</td><td>{row.item}</td><td>{row.lot || "—"}</td>
                <td className="k-quantity">{quantity(row.quantity)}</td><td>{row.problem}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <a className="btn" href={`${ENDPOINT}${delivery.id}/pick-list/pdf/`} target="_blank" rel="noopener">Pick list PDF</a>
    </section>
  );
}

/** What the customer signed for, and when: cement plants pay from their own receipt. */
function Received({ delivery, editable }: { delivery: Delivery; editable: boolean }) {
  const act = useAct<Delivery>();
  const [value, setValue] = useState({ received_on: delivery.received_on ?? "", received_by: delivery.received_by,
    reference: delivery.receipt_reference });
  const save = () => void act.run("POST", `${ENDPOINT}${delivery.id}/received/`, value, { done: "Receipt recorded" });
  return (
    <section className="related-list" aria-label="Received">
      <h2>Received</h2>
      <div className="field-grid">
        <Field label="Received on">
          {(fid) => editable
            ? <input id={fid} type="date" value={value.received_on} onChange={(e) => setValue({ ...value, received_on: e.target.value })} />
            : <output id={fid}>{delivery.received_on ? date(delivery.received_on) : "Not yet"}</output>}
        </Field>
        <Field label="Received by">
          {(fid) => editable
            ? <input id={fid} value={value.received_by} onChange={(e) => setValue({ ...value, received_by: e.target.value })} />
            : <output id={fid}>{delivery.received_by || "—"}</output>}
        </Field>
        <Field label="Their GRN">
          {(fid) => editable
            ? <input id={fid} value={value.reference} onChange={(e) => setValue({ ...value, reference: e.target.value })} />
            : <output id={fid}>{delivery.receipt_reference || "—"}</output>}
        </Field>
      </div>
      {editable && <ActionButton pending={act.pending} onClick={save}>Save receipt</ActionButton>}
    </section>
  );
}

/**
 * The paper the customer signed: a scan or a phone photograph, kept with the
 * receipt. It is never replaced in place: a better copy is added, the latest
 * is the one in force and the earlier ones stay listed under it. Kept by
 * whoever may record the receipt; removed as any attachment is.
 */
function ProofOfDelivery({ delivery, mayKeep }: { delivery: Delivery; mayKeep: boolean }) {
  const { can } = useAccess();
  const queries = useQueryClient();
  const rows = useGet<AttachmentRow[]>(ATTACHMENTS, { model: "sales.delivery", id: String(delivery.id), purpose: "proof_of_delivery" });
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // The attachments list and the history below both gain a line.
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["get", ATTACHMENTS] });
    void queries.invalidateQueries({ queryKey: ["get", "/api/core/history/"] });
  };
  const keep = async (file: File) => {
    const body = new FormData();
    body.append("model", "sales.delivery");
    body.append("id", String(delivery.id));
    body.append("purpose", "proof_of_delivery");
    body.append("file", file);
    setBusy(true);
    setProblem(null);
    try {
      await request(ATTACHMENTS, { method: "POST", body });
      refresh();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : "The signed copy could not be kept.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const remove = async (row: AttachmentRow) => {
    setProblem(null);
    try {
      await request(`${ATTACHMENTS}${row.id}/`, { method: "DELETE" });
      refresh();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : "The signed copy could not be removed.");
    }
  };
  const received = delivery.received_on !== null;
  return (
    <section className="related-list" aria-label="Proof of delivery">
      <h2>Proof of delivery</h2>
      {rows.isPending ? <p className="muted">…</p>
        : rows.isError ? <p className="muted">Could not read it: {rows.error.message}</p>
        : rows.data.length ? (
        <table className="data">
          <tbody>
            {rows.data.map((row) => (
              <tr key={row.id}>
                <td><a href={`${ATTACHMENTS}${row.id}/download/`}>{row.name}</a></td>
                <td>{row.current ? "The signed copy" : <span className="muted">An earlier copy</span>}</td>
                <td className="muted">{sizeOf(row.size)}</td>
                <td className="muted">{row.by} · {dateTime(row.at)}</td>
                <td>
                  {(row.mine || can("core.delete_attachment")) && (
                    <button type="button" className="icon-btn" aria-label={`Remove ${row.name}`} onClick={() => void remove(row)}>×</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">{received ? "No signed copy is kept yet." : "No signed copy is kept. Save the receipt first; the signed copy is kept with it."}</p>}
      {mayKeep && received && (
        <label className="btn">
          {busy ? "Keeping…" : rows.data?.length ? "Add a newer copy" : "Add the signed copy"}
          <input ref={input} type="file" hidden disabled={busy} accept=".pdf,.jpg,.jpeg,.png"
            onChange={(event) => { const file = event.target.files?.[0]; if (file) void keep(file); }} />
        </label>
      )}
      {mayKeep && received && <p className="muted">A scan or a photograph: PDF, JPEG or PNG, up to 20 MB.</p>}
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
}

/**
 * Who carried it, on what lorry receipt and vehicle: often known only after
 * the truck has gone, so kept apart from what the delivery moved and
 * recorded after it shipped too. The e-way bill and the transporter's
 * freight bill are matched by it.
 */
function Transport({ delivery, editable }: { delivery: Delivery; editable: boolean }) {
  const act = useAct<Delivery>();
  const [value, setValue] = useState({
    transporter: delivery.transporter, lr_number: delivery.lr_number, lr_date: delivery.lr_date ?? "",
    vehicle_number: delivery.vehicle_number,
  });
  const save = () => void act.run("POST", `${ENDPOINT}${delivery.id}/transport/`, value, { done: "Transport recorded" });
  return (
    <section className="related-list" aria-label="Transport">
      <h2>Transport</h2>
      <div className="field-grid">
        <Field label="Transporter">
          {(fid) => editable
            ? <PartyPicker id={fid} role="vendor" value={value.transporter} onChange={(v) => setValue({ ...value, transporter: v as number | null })} />
            : <output id={fid}>{delivery.transporter_name || "—"}</output>}
        </Field>
        <Field label="LR number">
          {(fid) => editable
            ? <input id={fid} value={value.lr_number} onChange={(e) => setValue({ ...value, lr_number: e.target.value })} />
            : <output id={fid}>{delivery.lr_number || "—"}</output>}
        </Field>
        <Field label="LR date">
          {(fid) => editable
            ? <input id={fid} type="date" value={value.lr_date} onChange={(e) => setValue({ ...value, lr_date: e.target.value })} />
            : <output id={fid}>{date(delivery.lr_date ?? undefined) || "—"}</output>}
        </Field>
        <Field label="Vehicle">
          {(fid) => editable
            ? <input id={fid} value={value.vehicle_number} onChange={(e) => setValue({ ...value, vehicle_number: e.target.value })} />
            : <output id={fid}>{delivery.vehicle_number || "—"}</output>}
        </Field>
      </div>
      {editable && <ActionButton pending={act.pending} onClick={save}>Save transport</ActionButton>}
    </section>
  );
}

interface Warehouse {
  id: number;
  code: string;
  name: string;
}

const ENDPOINT = "/api/sales/deliveries/";

/**
 * What leaves the gate. A draft's quantities can be cut to what is
 * actually on the lorry; posting takes the stock out and cannot be
 * undone except by a return, which brings it back.
 */
export default function DeliveryForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Delivery>(ENDPOINT, id);
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const act = useAct<Delivery>();
  const [returning, setReturning] = useState<Record<number, string> | null>(null);
  const [credit, setCredit] = useState(true);
  // Shipping takes the pick list away before the answer comes: the write
  // refreshes every open panel, and a pick list asked for once the goods
  // have gone is refused.
  const [shipping, setShipping] = useState(false);

  if (record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  const delivery = record.data;
  if (!delivery) return <div className="loading">Opening…</div>;

  const editable = !delivery.posted && can("sales.change_delivery");
  const warehouseName = (wid: number) => warehouses.data?.find((w) => w.id === wid)?.code ?? `#${wid}`;
  const state = delivery.reverses ? "Return" : delivery.posted ? "Shipped" : "Draft";

  const post = async () => {
    setShipping(true);
    const outcome = await act.run("POST", `${ENDPOINT}${delivery.id}/post_delivery/`, {}, {
      done: (result) => `${(result as Delivery).number} shipped`,
    });
    if (!outcome.ok) setShipping(false);
  };
  const backorder = async () => {
    const outcome = await act.run("POST", `${ENDPOINT}${delivery.id}/backorder/`, {}, { done: "Backorder drafted" });
    if (outcome.ok) navigate(`/sales/deliveries/${outcome.data.id}`);
  };
  const sendBack = async () => {
    const quantities = Object.fromEntries(Object.entries(returning ?? {}).filter(([, q]) => aboveZero(q)));
    if (Object.keys(quantities).length === 0) return;
    const outcome = await act.run("POST", `${ENDPOINT}${delivery.id}/customer_return/`, {
      quantities, credit_invoices: credit,
    }, { done: credit ? "Return posted and credited" : "Return posted" });
    if (outcome.ok) {
      setReturning(null);
      navigate(`/sales/deliveries/${outcome.data.id}`);
    }
  };

  return (
    <article className="doc">
      <DocHeader back="/sales/deliveries" backLabel="Deliveries" title="Delivery" number={delivery.number || "Draft delivery"}
        state={state} tone={delivery.reverses ? "info" : delivery.posted ? "done" : "draft"}>
        {!delivery.posted && can("sales.post_delivery") && (
          <ActionButton primary pending={act.pending} disabled={delivery.lines.length === 0} onClick={() => void post()}>Ship</ActionButton>
        )}
        {delivery.posted && !delivery.reverses && can("sales.post_delivery") && (
          <ActionButton pending={act.pending} onClick={() => void backorder()}>Backorder the rest</ActionButton>
        )}
        {delivery.posted && !delivery.reverses && can("sales.post_delivery") && returning === null && (
          <ActionButton pending={act.pending} onClick={() => setReturning({})}>Take goods back</ActionButton>
        )}
        <a className="btn" href={`${ENDPOINT}${delivery.id}/pdf/`} target="_blank" rel="noopener">PDF</a>
        {delivery.posted && can("sales.change_delivery") && (
          <ActionButton pending={act.pending} onClick={() => void act.run("POST", `${ENDPOINT}${delivery.id}/send/`, {}, {
            done: (result) => `Sent to ${(result as unknown as { sent_to: string }).sent_to}`,
          })}>Email</ActionButton>
        )}
      </DocHeader>

      <Sheet>
        <div className="field-grid">
          <Field label="Customer">{(fid) => <output id={fid}>{delivery.customer_name}</output>}</Field>
          <Field label="Order">{(fid) => <output id={fid}><Link to={`/sales/orders/${delivery.sales_order}`}>{delivery.order_number || "Open the order"}</Link></output>}</Field>
          <Field label="Date">{(fid) => <output id={fid}>{date(delivery.delivery_date)}</output>}</Field>
          <Field label="Reference">{(fid) => <output id={fid}>{delivery.reference || "—"}</output>}</Field>
          {delivery.reverses && <Field label="Returns">{(fid) => <output id={fid}><Link to={`/sales/deliveries/${delivery.reverses}`}>The original delivery</Link></output>}</Field>}
        </div>

        <div className="lines">
          <table>
            <thead>
              <tr>
                <th scope="col">Goods</th>
                <th scope="col">From</th>
                <th scope="col" className="k-quantity">Quantity</th>
                {returning !== null && <th scope="col" className="k-quantity">Coming back</th>}
              </tr>
            </thead>
            <tbody>
              {delivery.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td>{warehouseName(line.warehouse)}</td>
                  <td className="k-quantity">
                    {editable ? (
                      <CommitDecimal value={line.quantity_shipped} label={`Quantity of ${line.description}`}
                        onCommit={(q) => void act.run("PATCH", `/api/sales/delivery-lines/${line.id}/`, { quantity_shipped: q })} />
                    ) : quantity(line.quantity_shipped)}
                  </td>
                  {returning !== null && (
                    <td className="k-quantity">
                      <DecimalInput className="cell-input" aria-label={`Quantity of ${line.description} coming back`}
                        value={returning[line.id] ?? ""} onChange={(q) => setReturning({ ...returning, [line.id]: q })} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {editable && !delivery.reverses && can("manufacturing.change_bale") && <LoadBales delivery={delivery} />}
        {returning !== null && (
          <div className="return-box">
            <label className="check">
              <input type="checkbox" checked={credit} onChange={(e) => setCredit(e.target.checked)} />
              Credit the customer for what comes back (clear this for a replacement)
            </label>
            <ActionButton primary pending={act.pending} onClick={() => void sendBack()}>Post the return</ActionButton>
            <button type="button" className="btn" onClick={() => setReturning(null)}>Not now</button>
          </div>
        )}
      </Sheet>

      {!delivery.posted && !shipping && !delivery.reverses && delivery.lines.length > 0 && <PickList key={`pick-${delivery.id}-${delivery.lines.length}`} delivery={delivery} />}
      {!delivery.reverses && <Transport key={`transport-${delivery.id}-${delivery.lr_number}`} delivery={delivery} editable={can("sales.change_delivery")} />}
      {delivery.posted && !delivery.reverses && (
        <Received key={`received-${delivery.id}-${delivery.received_on ?? ""}`} delivery={delivery} editable={can("sales.change_delivery")} />
      )}
      {delivery.posted && !delivery.reverses && (
        <ProofOfDelivery delivery={delivery} mayKeep={can("sales.change_delivery") && can("core.add_attachment")} />
      )}
      {delivery && <Trail model="sales.delivery" id={delivery.id} />}
    </article>
  );
}
