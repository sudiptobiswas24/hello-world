import { Link } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Card {
  id: number; number: string; customer: string; title: string; value: string; chance: number; weighted: string;
  expected_on: string | null; owner: string; quotation: number | null;
}
interface Column { stage: string; label: string; cards: Card[] }

const ENDPOINT = "/api/sales/opportunities/";
const NEXT: Record<string, string> = { new: "qualified", qualified: "quoted" };

/**
 * What is in play, as columns by stage: each card the customer, the
 * business, what it is worth and when it is expected. A card moves to
 * the next stage from here; won and lost are said on its own page.
 */
export default function OpportunityBoard() {
  const { can } = useAccess();
  const board = useGet<Column[]>(`${ENDPOINT}board/`);
  const act = useAct();
  if (board.isError) return <ErrorPanel error={board.error} retry={() => void board.refetch()} />;
  const columns = board.data ?? [];
  const move = (card: Card, stage: string) =>
    void act.run("PATCH", `${ENDPOINT}${card.id}/`, { stage }, { done: `${card.number || card.title} moved to ${stage}` });
  return (
    <section className="report board-screen">
      <header className="list-head">
        <h1>Pipeline board</h1>
        <Link className="btn" to="/sales/pipeline">By the numbers</Link>
      </header>
      {board.isPending && <div className="loading">Reading the pipeline…</div>}
      <div className="board" style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(columns.length, 1)}, minmax(16rem, 1fr))`, gap: "1rem", alignItems: "start" }}>
        {columns.map((column) => (
          <section key={column.stage} className="sheet board-column" aria-label={column.label}>
            <h2>{column.label} <span className="muted">({column.cards.length})</span></h2>
            {column.cards.length === 0 && <p className="muted">None.</p>}
            {column.cards.map((card) => (
              <article key={card.id} className="board-card" style={{ border: "1px solid var(--line, #ddd)", borderRadius: "6px", padding: "0.5rem 0.75rem", marginBottom: "0.5rem" }}>
                <div><Link to={`/sales/opportunities/${card.id}`}><strong>{card.customer}</strong></Link>{card.number && <span className="muted"> · {card.number}</span>}</div>
                <div>{card.title}</div>
                <div className="muted">{money(card.value)} at {card.chance}% · {money(card.weighted)}</div>
                <div className="muted">{card.expected_on ? `Expected ${date(card.expected_on)}` : "No date yet"}{card.owner && ` · ${card.owner}`}</div>
                {NEXT[column.stage] && can("sales.change_opportunity") && (
                  <button type="button" className="btn" disabled={act.pending} onClick={() => move(card, NEXT[column.stage] ?? "")}>
                    Move to {NEXT[column.stage]}
                  </button>
                )}
              </article>
            ))}
          </section>
        ))}
      </div>
    </section>
  );
}
