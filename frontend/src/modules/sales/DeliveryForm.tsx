import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useAct, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { DecimalInput, Field } from "../../forms/fields";
import { aboveZero } from "../../lib/decimal";
import { date, quantity } from "../../lib/format";
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
                      <QuantityCell line={line} onCommit={(q) => void act.run("PATCH", `/api/sales/delivery-lines/${line.id}/`, { quantity_shipped: q })} />
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
    </article>
  );
}

function QuantityCell({ line, onCommit }: { line: DeliveryLine; onCommit: (q: string) => void }) {
  const [typed, setTyped] = useState<string | null>(null);
  const saved = quantity(line.quantity_shipped).replace(/,/g, "");
  return (
    <DecimalInput className="cell-input" aria-label={`Quantity of ${line.description}`} value={typed ?? saved} onChange={setTyped}
      onBlur={() => {
        if (typed !== null && typed !== "" && typed !== saved) onCommit(typed);
        setTyped(null);
      }} />
  );
}
