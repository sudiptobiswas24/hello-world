import { useState } from "react";
import { useNavigate } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { date, money, quantity } from "../../lib/format";

interface Line {
  id: number; requisition: number; requisition_number: string; requested_by: string; needed_by: string | null;
  item_label: string; uom: string; quantity: string; ordered: string; quoting: string; open: string;
  estimated_price: string | null; vendor: number | null; vendor_name: string;
}

/**
 * What approved requisitions still need and nobody has ordered or asked
 * about: tick the lines, say when answers are due, and one request for
 * quotation goes out for them, the vendor each line had in mind asked.
 */
export default function ToQuote() {
  const navigate = useNavigate();
  const lines = useGet<Line[]>("/api/purchasing/purchasing-reports/open-requisition-lines/");
  const act = useAct<{ rfq: number }>();
  const [picked, setPicked] = useState<number[]>([]);
  const [due, setDue] = useState("");
  const toggle = (id: number) => setPicked((now) => (now.includes(id) ? now.filter((one) => one !== id) : [...now, id]));
  const ask = async () => {
    const outcome = await act.run("POST", "/api/purchasing/purchasing-reports/request-quotes/",
      { lines: picked, response_due: due || undefined }, { done: "Out for quotes" });
    if (outcome.ok) navigate(`/purchasing/rfqs/${outcome.data.rfq}`);
  };
  const rows = lines.data ?? [];
  return (
    <article className="doc">
      <header className="list-head"><h1>To quote</h1></header>
      <section className="sheet">
        <p className="muted">Approved requisition lines nobody has ordered or asked about yet, soonest needed first. Tick what to ask about: one request for quotation goes out for all of it, to the vendors the lines had in mind.</p>
        {lines.isError ? <p className="muted">Could not read them: {lines.error.message}</p>
          : rows.length === 0 ? <p className="muted">{lines.isLoading ? "Reading…" : "Nothing waits to be quoted."}</p> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th /><th>Requisition</th><th>Item</th><th className="num">Open</th><th>Needed by</th><th className="num">About, each</th><th>In mind</th></tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td><input type="checkbox" aria-label={`Ask about ${row.item_label}`} checked={picked.includes(row.id)} onChange={() => toggle(row.id)} /></td>
                    <td>{row.requisition_number} · {row.requested_by}</td>
                    <td>{row.item_label}</td>
                    <td className="num">{quantity(row.open)} {row.uom}</td>
                    <td>{date(row.needed_by)}</td>
                    <td className="num">{row.estimated_price === null ? "" : money(row.estimated_price)}</td>
                    <td>{row.vendor_name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="actions">
          <label className="field">Answers by{" "}
            <input type="date" aria-label="Answers by" value={due} onChange={(event) => setDue(event.target.value)} />
          </label>
          <button type="button" className="primary" disabled={picked.length === 0 || act.pending} onClick={() => void ask()}>Ask for quotes</button>
        </div>
      </section>
    </article>
  );
}
