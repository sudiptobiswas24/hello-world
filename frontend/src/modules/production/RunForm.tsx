import { Link, useParams } from "react-router";

import { useAct, useGet, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { DocHeader, Sheet } from "../../forms/Document";
import { count, date, dateTime, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { Trail } from "../../views/Trail";

interface Run {
  id: number;
  warehouse_code: string;
  planned_on: string;
  horizon_end: string;
  ran_at: string;
  is_complete: boolean;
  notes: string;
  late: number[];
  expedites: number;
  defers: number;
  cancels: number;
}

interface PlannedOrder {
  id: number;
  item_sku: string;
  item_name: string;
  kind: "make" | "buy" | "transfer";
  quantity: string;
  needed_by: string;
  release_on: string;
  status: "suggested" | "firmed" | "cancelled";
  work_order: number | null;
  work_order_number: string;
  vendor_name: string;
  explanation: string;
  is_late: boolean;
  why_late: string;
}

interface Move {
  id: number;
  sentence: string;
  action: string;
  inside_fence: boolean;
}

interface Late {
  planned_order: number;
  item: string;
  needed_by: string;
  expected_on: string;
  days_behind: number;
  why: string;
}

interface Load {
  work_centre: number;
  code: string;
  week_beginning: string;
  available_minutes: string;
  booked_minutes: string;
  utilisation_percent: string | null;
}

const KIND: Record<string, string> = { make: "Make", buy: "Buy", transfer: "Move" };

/** One planning run: what it says to do, and why. */
export default function RunForm() {
  const { id } = useParams();
  const { can } = useAccess();
  const run = useRecord<Run>("/api/planning/runs/", id);
  const orders = useGet<PlannedOrder[]>(`/api/planning/runs/${id}/orders/`);
  const moves = useGet<Move[]>(`/api/planning/runs/${id}/actions/`);
  const late = useGet<Late[]>(`/api/planning/runs/${id}/late/`);
  const load = useGet<Load[]>(`/api/planning/runs/${id}/load/`);
  const act = useAct();

  if (run.isError) return <ErrorPanel error={run.error} retry={() => void run.refetch()} />;
  if (!run.data) return <div className="loading">Opening…</div>;
  const r = run.data;
  const mayFirm = can("planning.change_plannedorder");
  const suggested = (orders.data ?? []).filter((order) => order.status === "suggested");

  return (
    <article className="doc">
      <DocHeader back="/production/plan" backLabel="Plan" title="Planning run" number={`${r.warehouse_code} · ${dateTime(r.ran_at)}`}
        state={r.is_complete ? "Whole" : "Cut short"} tone={r.is_complete ? "done" : "warn"} />
      {r.notes && <div className="note warn" role="note">{r.notes}</div>}

      <div className="tiles">
        <div className="tile"><span>To decide</span><strong>{orders.data ? count(suggested.length) : "…"}</strong></div>
        <div className={`tile${r.late.length ? " bad" : ""}`}><span>Late already</span><strong>{count(r.late.length)}</strong></div>
        <div className="tile"><span>Pull in / push out / cancel</span><strong>{r.expedites} / {r.defers} / {r.cancels}</strong></div>
        <div className="tile"><span>Up to</span><strong>{date(r.horizon_end)}</strong></div>
      </div>

      <Sheet>
        <h2>What to make, buy and move</h2>
        <div className="lines">
          <table>
            <thead>
              <tr>
                <th scope="col">Item</th><th scope="col">How</th><th scope="col" className="k-quantity">Quantity</th>
                <th scope="col">Start by</th><th scope="col">Wanted</th><th scope="col">Why</th><th scope="col">State</th>
              </tr>
            </thead>
            <tbody>
              {(orders.data ?? []).map((order) => (
                <tr key={order.id} className={order.is_late ? "late" : undefined}>
                  <td><strong>{order.item_sku}</strong> {order.item_name}</td>
                  <td>{KIND[order.kind] ?? order.kind}{order.vendor_name ? ` · ${order.vendor_name}` : ""}</td>
                  <td className="k-quantity">{quantity(order.quantity)}</td>
                  <td>{date(order.release_on)}{order.is_late && <small className="bad"> {order.why_late}</small>}</td>
                  <td>{date(order.needed_by)}</td>
                  <td className="muted">{order.explanation}</td>
                  <td>
                    {order.status === "suggested" && mayFirm ? (
                      <span className="row-actions">
                        <button type="button" className="btn" disabled={act.pending}
                          onClick={() => void act.run("POST", `/api/planning/planned-orders/${order.id}/firm/`, {}, { done: `${order.item_sku} firmed` })}>Firm</button>
                        <button type="button" className="btn btn-quiet" disabled={act.pending}
                          onClick={() => void act.run("POST", `/api/planning/planned-orders/${order.id}/cancel/`, {}, { done: "Set aside" })}>Set aside</button>
                      </span>
                    ) : order.work_order ? (
                      <Link to={`/production/work-orders/${order.work_order}`}>{order.work_order_number}</Link>
                    ) : <span className={`pill pill-${order.status === "firmed" ? "done" : order.status === "cancelled" ? "cancelled" : "draft"}`}>{order.status}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {orders.data && orders.data.length === 0 && <p className="muted">Nothing to make or buy: what is wanted is covered.</p>}
        </div>
      </Sheet>

      {(moves.data?.length ?? 0) > 0 && (
        <section className="sheet">
          <h2>Orders already out, dated wrong</h2>
          <ul className="sentences">
            {moves.data!.map((move) => <li key={move.id}>{move.sentence}{move.inside_fence && <span className="pill pill-warn">inside the fence</span>}</li>)}
          </ul>
        </section>
      )}

      {(late.data?.length ?? 0) > 0 && (
        <section className="sheet">
          <h2>Cannot be ready in time</h2>
          <ul className="sentences">
            {late.data!.map((row) => (
              <li key={row.planned_order}><strong>{row.item}</strong>: wanted {date(row.needed_by)}, ready {date(row.expected_on)}, {row.days_behind} days behind. {row.why}</li>
            ))}
          </ul>
        </section>
      )}

      {(load.data?.length ?? 0) > 0 && (
        <section className="sheet">
          <h2>How full the machines are</h2>
          <table>
            <thead><tr><th scope="col">Work centre</th><th scope="col">Week of</th><th scope="col" className="k-quantity">Booked</th><th scope="col" className="k-quantity">Available</th><th scope="col" className="k-quantity">Used</th></tr></thead>
            <tbody>
              {load.data!.map((row) => (
                <tr key={`${row.work_centre}-${row.week_beginning}`}>
                  <td>{row.code}</td><td>{date(row.week_beginning)}</td>
                  <td className="k-quantity">{quantity(row.booked_minutes, 0)} min</td>
                  <td className="k-quantity">{quantity(row.available_minutes, 0)} min</td>
                  <td className="k-quantity">{row.utilisation_percent === null ? "—" : `${quantity(row.utilisation_percent, 0)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {r && <Trail model="planning.planningrun" id={r.id} />}
    </article>
  );
}
