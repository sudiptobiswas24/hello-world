import { useState } from "react";

import { useAct, useGet, useRows } from "../../api/hooks";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Item {
  label: string; item_type: string; account_code: string; party_name: string; currency_code: string;
  open_amount: string; carried_base: string; rate: string | null;
}
interface Preview { as_of: string; items: Item[] }
interface Line {
  id: number; label: string; party_name: string; account_code: string; currency_code: string; open_amount: string;
  rate: string; carried_base: string; restated_base: string; difference: string;
}
interface Restatement {
  id: number; number: string; as_of: string; reverses_on: string; voided_on: string | null; lines: Line[];
  rates: { currency_code: string; rate: string; rate_from: string | null }[];
}

/**
 * Foreign balances restated at the closing rate on a day (accounting.restatement): what was open, the
 * base it was carried at, the base at the closing rate, and the difference posted. It reverses on the
 * next day; a standing one is voided here.
 */
export default function Restatements() {
  const [asOf, setAsOf] = useState("");
  const act = useAct<Restatement>();
  const preview = useGet<Preview>("/api/accounting/restatements/preview/", { as_of: asOf }, Boolean(asOf));
  const made = useRows<Restatement>("/api/accounting/restatements/", {});
  return (
    <section className="report">
      <header className="page-head">
        <h1>Restate foreign balances</h1>
        <label className="inline">As at <input type="date" aria-label="As at" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <button type="button" disabled={!asOf || act.pending}
          onClick={() => void act.run("POST", "/api/accounting/restatements/restate/", { as_of: asOf },
            { done: (result) => `${result.number} restates ${result.as_of}.` })}>Restate</button>
      </header>
      {preview.isError ? <ErrorPanel error={preview.error} retry={() => void preview.refetch()} /> : preview.data && (
        <div className="table-wrap">
          <table aria-label="Open in a foreign currency">
            <thead><tr><th scope="col">Item</th><th scope="col">Party</th><th scope="col">Account</th>
              <th scope="col">Currency</th><th scope="col" className="k-money">Open</th>
              <th scope="col" className="k-money">Carried</th><th scope="col" className="k-money">Rate</th></tr></thead>
            <tbody>
              {preview.data.items.length === 0 ? <tr><td colSpan={7}>Nothing in a foreign currency is open on that day.</td></tr>
                : preview.data.items.map((item) => (
                  <tr key={`${item.item_type}-${item.label}`}>
                    <td>{item.label}</td><td>{item.party_name}</td><td>{item.account_code}</td><td>{item.currency_code}</td>
                    <td className="k-money">{money(item.open_amount)}</td><td className="k-money">{money(item.carried_base)}</td>
                    <td className="k-money">{item.rate ?? "none"}</td>
                  </tr>))}
            </tbody>
          </table>
        </div>
      )}
      {made.isError ? <ErrorPanel error={made.error} retry={() => void made.refetch()} /> : (made.data ?? []).map((row) => (
        <article key={row.id} className="card" aria-label={row.number}>
          <h2>{row.number} at {row.as_of} {row.voided_on
            ? <span className="pill pill-bad">Void from {row.voided_on}</span>
            : <span className="pill pill-done">Reverses {row.reverses_on}</span>}</h2>
          <p className="muted">{row.rates.map((rate) => `${rate.currency_code} ${rate.rate}${rate.rate_from ? ` (rate of ${rate.rate_from})` : " (given)"}`).join(", ")}</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Item</th><th scope="col">Party</th><th scope="col">Account</th>
                <th scope="col" className="k-money">Open</th><th scope="col" className="k-money">Carried</th>
                <th scope="col" className="k-money">Restated</th><th scope="col" className="k-money">Difference</th></tr></thead>
              <tbody>
                {row.lines.map((line) => (
                  <tr key={line.id}>
                    <td>{line.label}</td><td>{line.party_name}</td><td>{line.account_code}</td>
                    <td className="k-money">{money(line.open_amount)} {line.currency_code}</td>
                    <td className="k-money">{money(line.carried_base)}</td><td className="k-money">{money(line.restated_base)}</td>
                    <td className="k-money">{money(line.difference)}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
          {!row.voided_on && (
            <button type="button" disabled={act.pending}
              onClick={() => void act.run("POST", `/api/accounting/restatements/${row.id}/void/`, { date: row.as_of },
                { done: `${row.number} is void.` })}>Void {row.number}</button>
          )}
        </article>
      ))}
    </section>
  );
}
