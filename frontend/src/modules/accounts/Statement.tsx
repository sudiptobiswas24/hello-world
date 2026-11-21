import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router";

import { money } from "../../lib/format";

export interface Row {
  account: string;
  account_id: number;
  name: string;
  balance: string;
  natural?: string;
  opening?: string;
  debit?: string;
  credit?: string;
}

/** A date or a period kept in the address, so a statement can be sent on. */
export function usePeriod(keys: string[], defaults: Record<string, string>) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(keys.map((key) => [key, params.get(key) ?? defaults[key] ?? ""]));
  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });
  return { values, set };
}

/** One section of a statement: accounts, each opening on its ledger, and a total. */
export function Section({ title, rows, total, query, natural = true }: {
  title: string; rows: Row[]; total?: string; query: string; natural?: boolean;
}) {
  return (
    <section className="statement-section">
      <h2>{title}</h2>
      <table>
        <tbody>
          {rows.map((row) => (
            <tr key={row.account}>
              <td className="code"><Link to={`/accounts/chart/${row.account_id}${query}`}>{row.account}</Link></td>
              <td>{row.name}</td>
              <td className="k-money">{money(natural ? row.natural ?? row.balance : row.balance)}</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={3} className="muted">Nothing.</td></tr>}
        </tbody>
        {total !== undefined && (
          <tfoot><tr><th colSpan={2} scope="row">Total {title.toLowerCase()}</th><td className="k-money">{money(total)}</td></tr></tfoot>
        )}
      </table>
    </section>
  );
}

export function StatementHead({ title, children, verdict }: { title: string; children: ReactNode; verdict?: ReactNode }) {
  return (
    <header className="list-head">
      <h1>{title}</h1>
      {children}
      {verdict}
    </header>
  );
}
