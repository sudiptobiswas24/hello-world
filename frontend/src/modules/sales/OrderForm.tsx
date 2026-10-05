import { useState } from "react";
import { useNavigate, useParams } from "react-router";

import { useAct, useGet, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Steps, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { RecordPicker } from "../../forms/RecordPicker";
import { useDraft } from "../../forms/useDraft";
import { aboveZero } from "../../lib/decimal";
import { date, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RelatedList } from "./Related";

export interface Order {
  id: number;
  number: string;
  customer: number | null;
  customer_name: string;
  order_date: string;
  reference: string;
  status: "draft" | "confirmed" | "cancelled";
  lines: (TradeLine & { quantity_shipped: string; quantity_invoiced: string; quantity_open: string })[];
  subtotal: string;
  tax_total: string;
  total: string;
  delivery_status: string;
  invoice_status: string;
  [key: string]: unknown;
}

interface Party {
  id: number;
  code: string;
  name: string;
}

interface Approval {
  status: string;
  reasons: string[];
}

const ENDPOINT = "/api/sales/sales-orders/";

function stage(order: Order): number {
  if (order.status === "draft") return 0;
  if (order.invoice_status === "full") return 3;
  if (order.delivery_status === "full") return 2;
  return 1;
}

export function CustomerPicker({ value, onChange, invalid, id, disabled }: {
  value: number | null;
  onChange: (id: number | null) => void;
  invalid?: boolean;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <RecordPicker<Party>
      id={id}
      endpoint="/api/core/parties/"
      fixed={{ role_assignments__role: "customer", is_active: "true" }}
      value={value}
      onChange={(next) => onChange(next)}
      label={(row) => row.name}
      detail={(row) => row.code}
      placeholder="Type a customer's name or code"
      invalid={invalid}
      disabled={disabled}
    />
  );
}

/**
 * A sales order from its draft to fully invoiced. While it is a draft
 * everything on it can change; once confirmed it is shipped against and
 * invoiced, and changes to what was promised are the server's to refuse.
 */
export default function OrderForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Order>(ENDPOINT, id);
  const order = record.data;
  const draft = useDraft<Order>(isNew ? ({ customer: null, order_date: today(), reference: "" } as unknown as Order) : order);
  const act = useAct<Order>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !order) return <div className="loading">Opening…</div>;

  const editable = isNew || (order?.status === "draft" && can("sales.change_salesorder"));
  const value = draft.value;

  const save = async () => {
    if (isNew) {
      const outcome = await act.run("POST", ENDPOINT, {
        customer: value.customer, order_date: value.order_date, reference: value.reference,
      }, { done: "Order created" });
      if (outcome.ok) {
        draft.reset();
        navigate(`/sales/orders/${outcome.data.id}`, { replace: true });
      } else draft.failed(outcome.error);
      return;
    }
    const outcome = await act.run("PATCH", `${ENDPOINT}${order!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) draft.reset();
    else draft.failed(outcome.error);
  };

  const run = (action: string, done: string, then?: (result: unknown) => void, body?: unknown) =>
    act.run("POST", `${ENDPOINT}${order!.id}/${action}/`, body ?? {}, { done, onDone: then as never });

  return (
    <article className="doc">
      <DocHeader
        back="/sales/orders"
        backLabel="Orders"
        title="New sales order"
        number={order?.number}
        state={order ? order.status : undefined}
        tone={order?.status === "confirmed" ? "confirmed" : order?.status === "cancelled" ? "cancelled" : "draft"}
      >
        {editable && (draft.dirty || isNew) && (
          <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>
        )}
        {order?.status === "draft" && !draft.dirty && can("sales.change_salesorder") && (
          <ActionButton primary pending={act.pending} disabled={order.lines.length === 0} onClick={() => void run("confirm", `${order.number || "Order"} confirmed`)}>
            Confirm
          </ActionButton>
        )}
        {order?.status === "draft" && can("sales.approve_order") && (
          <ActionButton pending={act.pending} onClick={() => void run("approve", "Approved")}>Approve</ActionButton>
        )}
        {order?.status === "confirmed" && order.delivery_status !== "full" && can("sales.add_delivery") && (
          <ShipButton order={order} pending={act.pending}
            ship={(warehouse) => void run("ship", "Delivery drafted", (d) => navigate(`/sales/deliveries/${(d as { id: number }).id}`),
              warehouse ? { warehouse } : {})} />
        )}
        {order?.status === "confirmed" && order.invoice_status !== "full" && can("sales.add_invoice") && (
          <ActionButton pending={act.pending} onClick={() => void run("create_invoice", "Invoice drafted", (inv) => navigate(`/sales/invoices/${(inv as { id: number }).id}`))}>
            Invoice
          </ActionButton>
        )}
        {order && order.status !== "cancelled" && can("sales.change_salesorder") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            if (window.confirm(`Cancel ${order.number || "this order"}? It cannot be undone.`)) void run("cancel", "Order cancelled");
          }}>
            Cancel order
          </ActionButton>
        )}
      </DocHeader>

      {order && order.status !== "cancelled" && <Steps steps={["Draft", "Confirmed", "Shipped", "Invoiced"]} at={stage(order)} />}
      {order?.status === "draft" && <ApprovalNote id={order.id} />}

      <Sheet>
        <div className="field-grid">
          <Field label="Customer" errors={draft.errors.customer}>
            {(fid) => editable
              ? <CustomerPicker id={fid} value={(value.customer as number | null) ?? null} invalid={!!draft.errors.customer} onChange={(v) => draft.set("customer", v as never)} />
              : <output id={fid}>{order?.customer_name}</output>}
          </Field>
          <Field label="Order date" errors={draft.errors.order_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.order_date ?? "")} onChange={(e) => draft.set("order_date", e.target.value as never)} />
              : <output id={fid}>{date(order?.order_date)}</output>}
          </Field>
          <Field label="Customer's reference" errors={draft.errors.reference}>
            {(fid) => editable
              ? <input id={fid} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} placeholder="Their PO number" />
              : <output id={fid}>{order?.reference || "—"}</output>}
          </Field>
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}

        {order && (
          <>
            <Lines
              lines={order.lines}
              endpoint="/api/sales/sales-order-lines/"
              parent="order"
              parentId={order.id}
              withUom
              editable={order.status === "draft" && can("sales.change_salesorder")}
              extra={order.status === "confirmed" ? [
                { label: "Shipped", render: (line) => quantity(line.quantity_shipped as string) },
                { label: "Invoiced", render: (line) => quantity(line.quantity_invoiced as string) },
              ] : []}
            />
            <Totals rows={[["Untaxed", order.subtotal], ["Tax", order.tax_total], ["Total", order.total, true]]} />
          </>
        )}
        {isNew && <p className="muted">Create the order, then add its lines.</p>}
      </Sheet>

      {order && order.status === "confirmed" && (
        <div className="related">
          <RelatedList title="Deliveries" endpoint="/api/sales/deliveries/" permission="sales.view_delivery" query={{ sales_order: order.id }}
            href={(row) => `/sales/deliveries/${row.id}`}
            cells={(row) => [String(row.number || "Draft"), date(String(row.delivery_date)), row.posted ? "Posted" : "Draft"]} />
          <RelatedList title="Invoices" endpoint="/api/sales/invoices/" permission="sales.view_invoice" query={{ sales_order: order.id }}
            href={(row) => `/sales/invoices/${row.id}`}
            cells={(row) => [String(row.number || "Draft"), date(String(row.invoice_date)), money(String(row.total))]} />
        </div>
      )}
    </article>
  );
}

function ApprovalNote({ id }: { id: number }) {
  const { data } = useGet<Approval>(`${ENDPOINT}${id}/approval/`);
  if (!data || !data.reasons?.length || data.status === "approved") return null;
  return (
    <div className="note warn" role="note">
      <strong>Needs approval before it can be confirmed.</strong>
      <ul>{data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </div>
  );
}

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
 * Ship asks where from only when it has to: a line that names its
 * warehouse ships from it, and where only one warehouse can ship at
 * all, that one is meant. Quarantine, transit, consignment and a
 * customer's own material never ship on a delivery; the server refuses
 * them at posting, so they are not offered here.
 */
function ShipButton({ order, pending, ship }: { order: Order; pending: boolean; ship: (warehouse: number | null) => void }) {
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const [asking, setAsking] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const shippable = (warehouses.data ?? []).filter((w) =>
    w.is_active && !w.is_quarantine && !w.is_transit && w.consignment_vendor === null && w.held_for === null);
  const needsOne = order.lines.some((line) => !line.charge && aboveZero(line.quantity_open as string) && !line.warehouse);

  const start = () => {
    if (!needsOne) ship(null);
    else if (shippable.length === 1) ship(shippable[0]!.id);
    else setAsking(true);
  };

  if (!asking) return <ActionButton primary pending={pending || (needsOne && warehouses.isPending)} onClick={start}>Ship</ActionButton>;
  return (
    <span className="ship-from">
      <label>
        <span>Ship from</span>
        <select autoFocus value={chosen ?? ""} onChange={(e) => setChosen(Number(e.target.value) || null)}>
          <option value="">Choose a warehouse…</option>
          {shippable.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
        </select>
      </label>
      <ActionButton primary pending={pending} disabled={chosen === null} onClick={() => ship(chosen)}>Draft delivery</ActionButton>
      <button type="button" className="btn btn-quiet" onClick={() => setAsking(false)}>Back</button>
    </span>
  );
}
