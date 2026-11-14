import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { type Row, StatementHead, usePeriod } from "./Statement";
import { Link } from "react-router";

interface Trial {
  balanced: boolean;
  total_debit: string;
  total_credit: string;
  rows: Row[];
}

/** Every account's balance at a date, debits against credits. */
export default function TrialBalance() {
  const { values, set } = usePeriod(["as_of", "start"], { as_of: today() });
  const report = useGet<Trial>("/api/accounting/financial-statements/trial-balance/",
    { as_of: values.as_of!, ...(values.start ? { start: values.start } : {}) });
  const data = report.data;
  const toLedger = `?to=${values.as_of}${values.start ? `&from=${values.start}` : ""}`;
  return (
    <section className="report">
      <StatementHead title="Trial balance" verdict={data && (
        <span className={`pill pill-${data.balanced ? "done" : "bad"}`}>{data.balanced ? "Balances" : "Does not balance"}</span>
      )}>
        <label className="inline">Movement from <input type="date" value={values.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="inline">As at <input type="date" value={values.as_of} onChange={(e) => set("as_of", e.target.value)} /></label>
      </StatementHead>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Account</th><th scope="col"></th>
                {values.start && <th scope="col" className="k-money">Opening</th>}
                <th scope="col" className="k-money">Debit</th><th scope="col" className="k-money">Credit</th>
                <th scope="col" className="k-money">Balance</th>
              </tr>
            </thead>
            <tbody>
              {!data ? <tr className="skeleton"><td colSpan={6}><span /></td></tr> : data.rows.map((row) => (
                <tr key={row.account}>
                  <td><Link to={`/accounts/chart/${row.account_id}${toLedger}`}>{row.account}</Link></td>
                  <td>{row.name}</td>
                  {values.start && <td className="k-money">{money(row.opening)}</td>}
                  <td className="k-money">{money(row.debit)}</td>
                  <td className="k-money">{money(row.credit)}</td>
                  <td className="k-money">{money(row.balance)}</td>
                </tr>
              ))}
            </tbody>
            {data && (
              <tfoot>
                <tr>
                  <th colSpan={values.start ? 3 : 2} scope="row">Total</th>
                  <td className="k-money">{money(data.total_debit)}</td>
                  <td className="k-money">{money(data.total_credit)}</td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </section>
  );
}
