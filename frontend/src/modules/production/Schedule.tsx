import { Link } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { date, dateTime, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Slot {
  work_order: number;
  run: string;
  item: string;
  operation: string;
  sequence: number;
  start: string;
  finish: string;
  changeover_minutes: string;
  purge_kg: string;
  late: boolean;
  due: string | null;
  held: boolean;
  moved_from: string | null;
}

/**
 * Each machine's queue, in the order the floor should take it: start
 * and finish, the changeover between jobs and the purge it costs, and
 * what will finish after it is due. Committing writes these times onto
 * the runs' steps, so the stations see them.
 */
export default function Schedule() {
  const { can } = useAccess();
  const board = useGet<Record<string, Slot[]>>("/api/manufacturing/dispatch/");
  const act = useAct();

  if (board.isError) return <ErrorPanel error={board.error} retry={() => void board.refetch()} />;
  const machines = Object.entries(board.data ?? {});
  return (
    <section className="report">
      <header className="list-head">
        <h1>Machine schedule</h1>
        {can("manufacturing.change_workorderoperation") && machines.length > 0 && (
          <ActionButton primary pending={act.pending} onClick={() => {
            if (window.confirm("Write these times onto the runs' steps?")) void act.run("POST", "/api/manufacturing/dispatch/commit/", {}, { done: "Schedule committed" });
          }}>Commit schedule</ActionButton>
        )}
      </header>
      {board.isPending && <div className="loading">Scheduling…</div>}
      {board.data && machines.length === 0 && <div className="empty"><p>Nothing released to schedule.</p></div>}
      {machines.map(([machine, slots]) => (
        <section key={machine} className="sheet machine">
          <h2>{machine}</h2>
          <table>
            <thead><tr><th scope="col">Run</th><th scope="col">Item</th><th scope="col">Step</th><th scope="col">Starts</th><th scope="col">Finishes</th><th scope="col" className="k-quantity">Changeover</th><th scope="col">Due</th></tr></thead>
            <tbody>
              {slots.map((slot) => (
                <tr key={`${slot.work_order}-${slot.sequence}`} className={slot.late ? "late" : undefined}>
                  <td><Link to={`/production/work-orders/${slot.work_order}`}>{slot.run}</Link></td>
                  <td>{slot.item}</td>
                  <td>{slot.operation}</td>
                  <td>{dateTime(slot.start)}</td>
                  <td>{dateTime(slot.finish)}{slot.late && <span className="pill pill-bad">late</span>}</td>
                  <td className="k-quantity">{quantity(slot.changeover_minutes, 0)} min{slot.purge_kg !== "0" ? ` · ${quantity(slot.purge_kg)} kg purge` : ""}</td>
                  <td>{date(slot.due)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </section>
  );
}
