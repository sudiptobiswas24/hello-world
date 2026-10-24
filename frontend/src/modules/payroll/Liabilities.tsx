import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { StatementHead, usePeriod } from "../accounts/Statement";

interface Row {
  account_code: string;
  account_name: string;
  period: string;
  deducted: string;
  remitted: string;
  outstanding: string;
  due_date: string | null;
  overdue: boolean;
}

/** What payroll took from people for the government and has not yet paid over. */
export default function Liabilities() {
  const { values, set } = usePeriod(["as_of"], { as_of: today() });
  const report = useGet<Row[]>("/api/hr/statutory-liabilities/", { as_of: values.as_of! });
  return (
    <section className="report">
      <StatementHead title="Statutory dues">
        <label className="inline">As at <input type="date" value={values.as_of} onChange={(e) => set("as_of", e.target.value)} /></label>
      </StatementHead>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Account</th><th scope="col">Month</th><th scope="col" className="k-money">Deducted</th><th scope="col" className="k-money">Paid over</th><th scope="col" className="k-money">Owed</th><th scope="col">Due</th></tr></thead>
            <tbody>
              {(report.data ?? []).map((row) => (
                <tr key={`${row.account_code}-${row.period}`} className={row.overdue ? "late" : undefined}>
                  <td><strong>{row.account_code}</strong> {row.account_name}</td>
                  <td>{row.period.slice(0, 7)}</td>
                  <td className="k-money">{money(row.deducted)}</td>
                  <td className="k-money">{money(row.remitted)}</td>
                  <td className="k-money">{money(row.outstanding)}</td>
                  <td>{date(row.due_date)}{row.overdue && <span className="pill pill-bad">overdue</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.data && report.data.length === 0 && <div className="empty"><p>Nothing owed over.</p></div>}
        </div>
      )}
    </section>
  );
}
