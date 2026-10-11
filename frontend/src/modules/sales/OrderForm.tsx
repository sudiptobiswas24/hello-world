import { useNavigate, useParams } from "react-router";

import { useAct, useGet, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ExtraFields } from "../../forms/ExtraFields";
import { FieldSection } from "../../forms/FieldSection";
import { ActionButton, DocHeader, Sheet, Steps, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { PartyPicker } from "../../forms/PartyPicker";
import { useDraft } from "../../forms/useDraft";
import { WarehouseChoice } from "../../forms/WarehouseChoice";
import { aboveZero } from "../../lib/decimal";
import { date, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { useRefs } from "../../views/RecordScreen";
import { orderTermFields } from "../parties/tradeTerms";
import { RelatedList } from "../../forms/Related";
import { Trail } from "../../views/Trail";
import { SmartButtons } from "../../views/SmartButtons";
import { orderButtons } from "./smart";

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

interface Approval {
  status: string;
  reasons: string[];
}

const TERM_FIELDS = orderTermFields("sell");
const ENDPOINT = "/api/sales/sales-orders/";

function stage(order: Order): number {
  if (order.status === "draft") return 0;
  if (order.invoice_status === "full") return 3;
  if (order.delivery_status === "full") return 2;
  return 1;
}

/** Kept for the sales screens that import it from here. */
export function CustomerPicker(props: Omit<Parameters<typeof PartyPicker>[0], "role">) {
  return <PartyPicker role="customer" {...props} />;
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
  const approval = useGet<Approval>(`${ENDPOINT}${order?.id}/approval/`, undefined, order?.status === "draft");
  const waiting = approval.data?.status === "pending";

  const refs = useRefs(TERM_FIELDS);
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
        {order?.status === "draft" && waiting && can("sales.approve_order") && (
          <ActionButton pending={act.pending} onClick={() => void run("approve", "Approved")}>Approve</ActionButton>
        )}
        {order?.status === "confirmed" && order.delivery_status !== "full" && can("sales.add_delivery") && (
          <WarehouseChoice label="Ship" prompt="Ship from" confirm="Draft delivery" pending={act.pending}
            needsOne={order.lines.some((line) => !line.charge && aboveZero(line.quantity_open as string) && !line.warehouse)}
            go={(warehouse) => void run("ship", "Delivery drafted", (d) => navigate(`/sales/deliveries/${(d as { id: number }).id}`),
              warehouse ? { warehouse } : {})} />
        )}
        {order?.status === "confirmed" && order.invoice_status !== "full" && can("sales.add_invoice") && (
          <ActionButton pending={act.pending} onClick={() => void run("create_invoice", "Invoice drafted", (inv) => navigate(`/sales/invoices/${(inv as { id: number }).id}`))}>
            Invoice
          </ActionButton>
        )}
        {order && order.status !== "cancelled" && order.lines.length > 0 && !draft.dirty && (
          <>
            {order.status === "confirmed" && <a className="btn" href={`${ENDPOINT}${order.id}/pdf/?kind=acknowledgement`} target="_blank" rel="noopener">Acknowledgement PDF</a>}
            <a className="btn" href={`${ENDPOINT}${order.id}/pdf/?kind=proforma`} target="_blank" rel="noopener">Proforma PDF</a>
            {can("sales.change_salesorder") && order.status === "confirmed" && (
              <ActionButton pending={act.pending} onClick={() => void run("send", "Acknowledgement sent", undefined, { kind: "acknowledgement" })}>Email acknowledgement</ActionButton>
            )}
            {can("sales.change_salesorder") && (
              <ActionButton pending={act.pending} onClick={() => void run("send", "Proforma sent", undefined, { kind: "proforma" })}>Email proforma</ActionButton>
            )}
          </>
        )}
        {order && order.status !== "cancelled" && can("sales.change_salesorder") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            if (window.confirm(`Cancel ${order.number || "this order"}? It cannot be undone.`)) void run("cancel", "Order cancelled");
          }}>
            Cancel order
          </ActionButton>
        )}
      </DocHeader>
      {order && <SmartButtons buttons={orderButtons(order.id)} />}

      {order && order.status !== "cancelled" && <Steps steps={["Draft", "Confirmed", "Shipped", "Invoiced"]} at={stage(order)} />}
      {order?.status === "draft" && waiting && (approval.data?.reasons.length ?? 0) > 0 && (
        <div className="note warn" role="note">
          <strong>Needs approval before it can be confirmed.</strong>
          <ul>{approval.data!.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      )}

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
        {/* The order's own, taken from the party's terms when it was made: a new order has them once it is saved. */}
        {!isNew && <FieldSection title="Terms" fields={TERM_FIELDS} value={value} set={(key, next) => draft.set(key, next as never)}
          errors={draft.errors} editable={editable} refs={refs} />}
        <ExtraFields kind="sales.salesorder" value={value.extra as Record<string, unknown> | undefined} set={(next) => draft.set("extra", next as never)} errors={draft.errors} editable={editable} />
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
              closable={order.status === "confirmed" && can("sales.change_salesorder")}
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
      {order && <Trail model="sales.salesorder" id={order.id} />}
    </article>
  );
}
