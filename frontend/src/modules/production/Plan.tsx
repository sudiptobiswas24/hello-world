import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { useAct, useReference, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { DecimalInput } from "../../forms/fields";
import { count, date, dateTime } from "../../lib/format";

interface Run {
  id: number;
  warehouse_code: string;
  planned_on: string;
  horizon_end: string;
  ran_at: string;
  is_complete: boolean;
  late: number[];
  lapsed: number[];
  overloaded: number[];
  expedites: number;
  defers: number;
  cancels: number;
}

interface Warehouse {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  is_quarantine: boolean;
  is_transit: boolean;
}

/**
 * Planning: run it for a warehouse, then read what it says. The newest
 * run first; each one opens on what to make, buy and move, what is late
 * and why, and how full the machines are.
 */
export default function Plan() {
  const { can } = useAccess();
  const navigate = useNavigate();
  const runs = useRows<Run>("/api/planning/runs/", { ordering: "-ran_at", page_size: 20 });
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const act = useAct<Run>();
  const [warehouse, setWarehouse] = useState("");
  const [horizon, setHorizon] = useState("");
  const plantable = (warehouses.data ?? []).filter((w) => w.is_active && !w.is_quarantine && !w.is_transit);

  const planNow = async () => {
    const outcome = await act.run("POST", "/api/planning/runs/plan/", {
      warehouse: warehouse || plantable[0]?.id, horizon_days: horizon,
    }, { done: "Planned" });
    if (outcome.ok) navigate(`/production/plan/${outcome.data.id}`);
  };

  return (
    <section className="report">
      <header className="list-head">
        <h1>Plan</h1>
        {can("planning.add_planningrun") && (
          <span className="ship-from">
            <label>
              <span>For</span>
              <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} aria-label="Warehouse to plan">
                {plantable.map((w) => <option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}
              </select>
            </label>
            <label>
              <span>Days ahead</span>
              <DecimalInput places={0} value={horizon} onChange={setHorizon} placeholder="As set" aria-label="Days ahead" className="qty" />
            </label>
            <ActionButton primary pending={act.pending} disabled={plantable.length === 0} onClick={() => void planNow()}>Plan now</ActionButton>
          </span>
        )}
      </header>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Run</th><th scope="col">For</th><th scope="col">Up to</th>
              <th scope="col" className="k-quantity">Late</th><th scope="col" className="k-quantity">Pull in</th>
              <th scope="col" className="k-quantity">Push out</th><th scope="col" className="k-quantity">Cancel</th>
              <th scope="col" className="k-quantity">No room</th><th scope="col">Whole?</th>
            </tr>
          </thead>
          <tbody>
            {runs.isPending ? <tr className="skeleton"><td colSpan={9}><span /></td></tr> : (runs.data ?? []).map((run) => (
              <tr key={run.id}>
                <td><Link to={`/production/plan/${run.id}`}>{dateTime(run.ran_at)}</Link></td>
                <td>{run.warehouse_code}</td>
                <td>{date(run.horizon_end)}</td>
                <td className="k-quantity">{count(run.late.length)}</td>
                <td className="k-quantity">{count(run.expedites)}</td>
                <td className="k-quantity">{count(run.defers)}</td>
                <td className="k-quantity">{count(run.cancels)}</td>
                <td className="k-quantity">{count(run.overloaded.length)}</td>
                <td>{run.is_complete ? "Yes" : <span className="pill pill-warn">Cut short</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.data && runs.data.length === 0 && <div className="empty"><p>Nothing planned yet.</p></div>}
      </div>
    </section>
  );
}
