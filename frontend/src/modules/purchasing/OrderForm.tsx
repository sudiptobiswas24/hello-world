import { useNavigate, useParams } from "react-router";

import { useAct, useGet, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Steps, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { PartyPicker } from "../../forms/PartyPicker";
import { RelatedList } from "../../forms/Related";
import { useDraft } from "../../forms/useDraft";
import { WarehouseChoice } from "../../forms/WarehouseChoice";
import { aboveZero } from "../../lib/decimal";
import { date, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface PurchaseOrder {
  id: number;
  number: string;
  vendor: number | null;
  vendor_name: string;
  order_date: string;
  reference: string;
  status: "draft" | "confirmed" | "cancelled";
  lines: (TradeLine & { quantity_received: string; quantity_billed: string; quantity_open: string; warehouse: number | null })[];
  subtotal: string;
  tax_total: string;
  total: string;
  receipt_status: string;
  bill_status: string;
  [key: string]: unknown;
}

interface Approval {
  status: string;
  reasons: string[];
}

const ENDPOINT = "/api/purchasing/purchase-orders/";

function stage(order: PurchaseOrder): number {
  if (order.status === "draft") return 0;
  if (order.bill_status === "full") return 3;
  if (order.receipt_status === "full") return 2;
  return 1;
}

/**
 * A purchase order from its draft to fully billed: the mirror of the
 * sales order. Where the policy asks for a signature, the order says
 * why, and only someone who may approve sees Approve; who may sign for
 * how much is the server's to decide.
 */
export default function OrderForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<PurchaseOrder>(ENDPOINT, id);
  const order = record.data;
  const draft = useDraft<PurchaseOrder>(isNew ? ({ vendor: null, order_date: today(), reference: "" } as unknown as PurchaseOrder) : order);
  const act = useAct<PurchaseOrder>();
  const approval = useGet<Approval>(`${ENDPOINT}${order?.id}/approval/`, undefined, order?.status === "draft");

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !order) return <div className="loading">Opening…</div>;

  const editable = isNew || (order?.status === "draft" && can("purchasing.change_purchaseorder"));
  const value = draft.value;
  const waiting = approval.data?.status === "pending";

  const save = async () => {
    if (isNew) {
      const outcome = await act.run("POST", ENDPOINT, {
        vendor: value.vendor, order_date: value.order_date, reference: value.reference,
      }, { done: "Order created" });
      if (outcome.ok) {
        draft.reset();
        navigate(`/purchasing/orders/${outcome.data.id}`, { replace: true });
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
      <DocHeader back="/purchasing/orders" backLabel="Purchase orders" title="New purchase order" number={order?.number}
        state={order ? order.status : undefined}
        tone={order?.status === "confirmed" ? "confirmed" : order?.status === "cancelled" ? "cancelled" : "draft"}>
        {editable && (draft.dirty || isNew) && (
          <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>
        )}
        {order?.status === "draft" && waiting && can("purchasing.approve_purchaseorder") && (
          <ActionButton pending={act.pending} onClick={() => void run("approve", "Approved")}>Approve</ActionButton>
        )}
        {order?.status === "draft" && !draft.dirty && can("purchasing.change_purchaseorder") && (
          <ActionButton primary pending={act.pending} disabled={order.lines.length === 0} onClick={() => void run("confirm", `${order.number || "Order"} confirmed`)}>
            Confirm
          </ActionButton>
        )}
        {order?.status === "confirmed" && order.receipt_status !== "full" && can("purchasing.add_goodsreceipt") && (
          <WarehouseChoice label="Receive" prompt="Arrives at" confirm="Draft receipt" pending={act.pending}
            needsOne={order.lines.some((line) => !line.charge && aboveZero(line.quantity_open) && !line.warehouse)}
            go={(warehouse) => void run("receive", "Receipt drafted", (r) => navigate(`/purchasing/goods-in/${(r as { id: number }).id}`),
              warehouse ? { warehouse } : {})} />
        )}
        {order?.status === "confirmed" && order.bill_status !== "full" && can("purchasing.add_bill") && (
          <ActionButton pending={act.pending} onClick={() => void run("create_bill", "Bill drafted", (bill) => navigate(`/purchasing/bills/${(bill as { id: number }).id}`))}>
            Bill
          </ActionButton>
        )}
        {order && order.status !== "cancelled" && can("purchasing.change_purchaseorder") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            if (window.confirm(`Cancel ${order.number || "this order"}? It cannot be undone.`)) void run("cancel", "Order cancelled");
          }}>
            Cancel order
          </ActionButton>
        )}
      </DocHeader>

      {order && order.status !== "cancelled" && <Steps steps={["Draft", "Confirmed", "Received", "Billed"]} at={stage(order)} />}
      {order?.status === "draft" && waiting && (approval.data?.reasons.length ?? 0) > 0 && (
        <div className="note warn" role="note">
          <strong>Needs approval before it can be confirmed.</strong>
          <ul>{approval.data!.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      )}

      <Sheet>
        <div className="field-grid">
          <Field label="Vendor" errors={draft.errors.vendor}>
            {(fid) => editable
              ? <PartyPicker role="vendor" id={fid} value={(value.vendor as number | null) ?? null} invalid={!!draft.errors.vendor} onChange={(v) => draft.set("vendor", v as never)} />
              : <output id={fid}>{order?.vendor_name}</output>}
          </Field>
          <Field label="Order date" errors={draft.errors.order_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.order_date ?? "")} onChange={(e) => draft.set("order_date", e.target.value as never)} />
              : <output id={fid}>{date(order?.order_date)}</output>}
          </Field>
          <Field label="Reference" errors={draft.errors.reference}>
            {(fid) => editable
              ? <input id={fid} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} placeholder="Their quotation number" />
              : <output id={fid}>{order?.reference || "—"}</output>}
          </Field>
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}

        {order && (
          <>
            <Lines lines={order.lines} endpoint="/api/purchasing/purchase-order-lines/" parent="order" parentId={order.id}
              withUom side="purchase" editable={order.status === "draft" && can("purchasing.change_purchaseorder")}
              closable={order.status === "confirmed" && can("purchasing.change_purchaseorder")}
              extra={order.status === "confirmed" ? [
                { label: "Received", render: (line) => quantity(line.quantity_received as string) },
                { label: "Billed", render: (line) => quantity(line.quantity_billed as string) },
              ] : []} />
            <Totals rows={[["Untaxed", order.subtotal], ["Tax", order.tax_total], ["Total", order.total, true]]} />
          </>
        )}
        {isNew && <p className="muted">Create the order, then add its lines. Prices come from the vendor's price list where one is agreed.</p>}
      </Sheet>

      {order && order.status === "confirmed" && (
        <div className="related">
          <RelatedList title="Goods in" endpoint="/api/purchasing/goods-receipts/" permission="purchasing.view_goodsreceipt"
            query={{ purchase_order: order.id }} href={(row) => `/purchasing/goods-in/${row.id}`}
            cells={(row) => [String(row.number || "Draft"), date(String(row.receipt_date)), row.reverses ? "Sent back" : row.posted ? "Received" : "Draft"]} />
          <RelatedList title="Bills" endpoint="/api/purchasing/bills/" permission="purchasing.view_bill"
            query={{ purchase_order: order.id }} href={(row) => `/purchasing/bills/${row.id}`}
            cells={(row) => [String(row.number || "Draft"), date(String(row.bill_date)), money(String(row.total))]} />
        </div>
      )}
    </article>
  );
}
