import { useState } from "react";
import { Trail } from "../../views/Trail";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, get } from "../../api/client";
import { useAct, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { ScanBox } from "../../views/ScanBox";
import { CommitDecimal, DecimalInput, Field } from "../../forms/fields";
import { aboveZero } from "../../lib/decimal";
import { date, quantity } from "../../lib/format";
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

  if (record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  const delivery = record.data;
  if (!delivery) return <div className="loading">Opening…</div>;

  const editable = !delivery.posted && can("sales.change_delivery");
  const warehouseName = (wid: number) => warehouses.data?.find((w) => w.id === wid)?.code ?? `#${wid}`;
  const state = delivery.reverses ? "Return" : delivery.posted ? "Shipped" : "Draft";

  const post = () => act.run("POST", `${ENDPOINT}${delivery.id}/post_delivery/`, {}, {
    done: (result) => `${(result as Delivery).number} shipped`,
  });
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
        {delivery.posted && !delivery.reverses && can("sales.add_delivery") && (
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

      {!delivery.reverses && <Transport key={`${delivery.id}-${delivery.lr_number}`} delivery={delivery} editable={can("sales.change_delivery")} />}
      {delivery.posted && !delivery.reverses && (
        <Received key={`${delivery.id}-${delivery.received_on ?? ""}`} delivery={delivery} editable={can("sales.change_delivery")} />
      )}
      {delivery && <Trail model="sales.delivery" id={delivery.id} />}
    </article>
  );
}
