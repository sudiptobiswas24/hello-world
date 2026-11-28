import { Link } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { date, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Card {
  id: number; number: string; status: string; item: string; quantity_ordered: string; quantity_produced: string; uom: string;
  starts: string | null; due: string | null; late: boolean; work_centre: string; machines: string[]; for: string;
}
interface Column { key: string; label: string; cards: Card[] }

const ENDPOINT = "/api/manufacturing/work-orders/";

/**
 * The floor's runs as columns: not released, released and waiting,
 * running, output complete and waiting to be closed, closed this week.
 * Each card is what the run has done; the release and the close are a
 * click away, the rest is on the run's own page.
 */
export default function Board() {
  const { can } = useAccess();
  const board = useGet<Column[]>(`${ENDPOINT}board/`);
  const act = useAct();
  if (board.isError) return <ErrorPanel error={board.error} retry={() => void board.refetch()} />;
  const columns = board.data ?? [];
  const run = (card: Card, path: "release" | "close") => {
    if (!window.confirm(path === "release" ? `Release ${card.number || "this run"}?` : `Close ${card.number}? Its output and inputs are costed as they stand.`)) return;
    void act.run("POST", `${ENDPOINT}${card.id}/${path}/`, {}, { done: path === "release" ? "Released" : "Closed" });
  };
  return (
    <section className="report board-screen">
      <header className="list-head"><h1>Run board</h1></header>
      {board.isPending && <div className="loading">Reading the floor…</div>}
      <div className="board" style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(14rem, 1fr))`, gap: "1rem", alignItems: "start" }}>
        {columns.map((column) => (
          <section key={column.key} className="sheet board-column" aria-label={column.label}>
            <h2>{column.label} <span className="muted">({column.cards.length})</span></h2>
            {column.cards.length === 0 && <p className="muted">None.</p>}
            {column.cards.map((card) => (
              <article key={card.id} className={`board-card${card.late ? " late" : ""}`} style={{ border: "1px solid var(--line, #ddd)", borderRadius: "6px", padding: "0.5rem 0.75rem", marginBottom: "0.5rem" }}>
                <div><Link to={`/production/work-orders/${card.id}`}><strong>{card.number || "Draft"}</strong></Link>{card.for && <span className="muted"> · for {card.for}</span>}</div>
                <div>{card.item}</div>
                <div className="muted">{quantity(card.quantity_produced)} of {quantity(card.quantity_ordered)} {card.uom}{card.work_centre && ` · ${card.work_centre}`}{card.machines.length > 0 && ` · ${card.machines.join(", ")}`}</div>
                <div className="muted">{card.due ? `Due ${date(card.due)}${card.late ? " (late)" : ""}` : "No due date"}</div>
                {column.key === "draft" && can("manufacturing.change_workorder") && (
                  <button type="button" className="btn" disabled={act.pending} onClick={() => run(card, "release")}>Release</button>
                )}
                {column.key === "to_close" && can("manufacturing.change_workorder") && (
                  <button type="button" className="btn" disabled={act.pending} onClick={() => run(card, "close")}>Close</button>
                )}
              </article>
            ))}
          </section>
        ))}
      </div>
    </section>
  );
}
