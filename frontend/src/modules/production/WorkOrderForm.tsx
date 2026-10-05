import { useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Steps } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { date, money, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Component {
  id: number;
  item_sku: string;
  item_name: string;
  quantity_required: string;
  quantity_issued: string;
}

interface Operation {
  id: number;
  sequence: number;
  name: string;
  is_outside: boolean;
  planned_minutes: string;
  minutes_booked: string;
  quantity_completed: string;
}

interface WorkOrder {
  id: number;
  number: string;
  item_sku: string;
  item_name: string;
  work_centre_code: string;
  quantity_ordered: string;
  quantity_produced: string;
  scheduled_start: string | null;
  scheduled_end: string | null;
  status: "draft" | "released" | "closed" | "cancelled";
  planned_unit_cost: string | null;
  wip_balance: string;
  conversion_variance: string | null;
  components: Component[];
  operations: Operation[];
  notes: string;
}

const ENDPOINT = "/api/manufacturing/work-orders/";
const AT: Record<string, number> = { draft: 0, released: 1, closed: 2 };

/**
 * One run: what it makes, what it draws, the steps it goes through and
 * what the floor has recorded against it. Release freezes the recipe and
 * cost against the shelf as it stands; close settles what is in progress
 * to the ledger; neither is undone by an edit.
 */
export default function WorkOrderForm() {
  const { id } = useParams();
  const { can } = useAccess();
  const record = useRecord<WorkOrder>(ENDPOINT, id);
  const act = useAct<WorkOrder>();

  if (record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  const order = record.data;
  if (!order) return <div className="loading">Opening…</div>;
  const may = can("manufacturing.change_workorder");
  const run = (action: string, done: string, body?: unknown) =>
    act.run("POST", `${ENDPOINT}${order.id}/${action}/`, body ?? {}, { done });

  return (
    <article className="doc">
      <DocHeader back="/production/work-orders" backLabel="Work orders" title="Work order" number={order.number || "Draft run"}
        state={order.status} tone={order.status === "closed" ? "done" : order.status === "cancelled" ? "cancelled" : order.status === "released" ? "confirmed" : "draft"}>
        {order.status === "draft" && may && (
          <ActionButton primary pending={act.pending} onClick={() => void run("release", "Released to the floor")}>Release</ActionButton>
        )}
        {order.status === "released" && may && (
          <ActionButton primary pending={act.pending} onClick={() => {
            if (window.confirm("Close this run? What is still in progress is settled to the ledger.")) void run("close", "Closed");
          }}>Close</ActionButton>
        )}
        {order.status === "closed" && may && (
          <ActionButton pending={act.pending} onClick={() => {
            const memo = window.prompt("Why reopen it?");
            if (memo !== null) void run("reopen", "Reopened", { memo });
          }}>Reopen</ActionButton>
        )}
        {order.status !== "draft" && (
          <a className="btn" href={`${ENDPOINT}${order.id}/traveller/`} target="_blank" rel="noopener">Job card</a>
        )}
        {(order.status === "draft" || order.status === "released") && may && (
          <ActionButton danger pending={act.pending} onClick={() => {
            if (window.confirm(`Cancel ${order.number || "this run"}?`)) void run("cancel", "Cancelled");
          }}>Cancel run</ActionButton>
        )}
      </DocHeader>
      {order.status !== "cancelled" && <Steps steps={["Draft", "On the floor", "Closed"]} at={AT[order.status] ?? 0} />}

      <Sheet>
        <div className="field-grid">
          <Field label="Makes">{(fid) => <output id={fid}><strong>{order.item_sku}</strong> {order.item_name}</output>}</Field>
          <Field label="Quantity">{(fid) => <output id={fid}>{quantity(order.quantity_ordered)} ordered · {quantity(order.quantity_produced)} made</output>}</Field>
          <Field label="Where">{(fid) => <output id={fid}>{order.work_centre_code || "—"}</output>}</Field>
          <Field label="When">{(fid) => <output id={fid}>{date(order.scheduled_start)} – {date(order.scheduled_end)}</output>}</Field>
          {order.status !== "draft" && (
            <Field label="In progress">{(fid) => <output id={fid}>{money(order.wip_balance)}</output>}</Field>
          )}
          {order.planned_unit_cost && <Field label="Planned cost a unit">{(fid) => <output id={fid}>{money(order.planned_unit_cost, 4)}</output>}</Field>}
        </div>

        <h2>What it draws</h2>
        <table>
          <thead><tr><th scope="col">Material</th><th scope="col" className="k-quantity">Needed</th><th scope="col" className="k-quantity">Issued</th></tr></thead>
          <tbody>
            {order.components.map((component) => (
              <tr key={component.id}>
                <td><strong>{component.item_sku}</strong> {component.item_name}</td>
                <td className="k-quantity">{quantity(component.quantity_required)}</td>
                <td className="k-quantity">{quantity(component.quantity_issued)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {order.operations.length > 0 && (
          <>
            <h2>Steps</h2>
            <table>
              <thead><tr><th scope="col">#</th><th scope="col">Step</th><th scope="col" className="k-quantity">Planned</th><th scope="col" className="k-quantity">Booked</th><th scope="col" className="k-quantity">Done</th></tr></thead>
              <tbody>
                {order.operations.map((operation) => (
                  <tr key={operation.id}>
                    <td>{operation.sequence}</td>
                    <td>{operation.name}{operation.is_outside && <span className="pill pill-info">outside</span>}</td>
                    <td className="k-quantity">{quantity(operation.planned_minutes, 0)} min</td>
                    <td className="k-quantity">{quantity(operation.minutes_booked, 0)} min</td>
                    <td className="k-quantity">{quantity(operation.quantity_completed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {order.notes && <p className="muted">{order.notes}</p>}
      </Sheet>
    </article>
  );
}
