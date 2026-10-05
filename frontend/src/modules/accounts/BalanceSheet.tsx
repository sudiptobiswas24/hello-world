import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { type Row, Section, StatementHead, usePeriod } from "./Statement";

interface Report {
  balanced: boolean;
  asset_total: string;
  total_liabilities_and_equity: string;
  retained_brought_forward: string;
  profit_for_year: string;
  assets: Row[];
  liabilities: Row[];
  equity: Row[];
}

/** What is owned and what is owed, at a date. */
export default function BalanceSheet() {
  const { values, set } = usePeriod(["as_of"], { as_of: today() });
  const report = useGet<Report>("/api/accounting/financial-statements/balance-sheet/", { as_of: values.as_of! });
  const data = report.data;
  const query = `?to=${values.as_of}`;
  return (
    <section className="report statement">
      <StatementHead title="Balance sheet" verdict={data && (
        <span className={`pill pill-${data.balanced ? "done" : "bad"}`}>{data.balanced ? "Balances" : "Does not balance"}</span>
      )}>
        <label className="inline">As at <input type="date" value={values.as_of} onChange={(e) => set("as_of", e.target.value)} /></label>
      </StatementHead>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : !data ? <div className="loading">Adding up…</div> : (
        <>
          <Section title="Assets" rows={data.assets} total={data.asset_total} query={query} />
          <Section title="Liabilities" rows={data.liabilities} query={query} />
          <Section title="Equity" rows={data.equity} query={query} />
          <table className="statement-foot">
            <tbody>
              <tr><th scope="row">Retained from earlier years</th><td className="k-money">{money(data.retained_brought_forward)}</td></tr>
              <tr><th scope="row">Profit this year</th><td className="k-money">{money(data.profit_for_year)}</td></tr>
              <tr><th scope="row">Total liabilities and equity</th><td className="k-money"><strong>{money(data.total_liabilities_and_equity)}</strong></td></tr>
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
