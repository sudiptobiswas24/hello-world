import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { isNegative, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { type Row, Section, StatementHead, usePeriod } from "./Statement";

interface Report {
  income_total: string;
  expense_total: string;
  net_profit: string;
  income: Row[];
  expenses: Row[];
}

function yearStart(day: string): string {
  // The Indian year starts in April.
  const [year, month] = day.split("-").map(Number);
  return `${month! >= 4 ? year : year! - 1}-04-01`;
}

/** What was earned and spent over a period, and what is left. */
export default function ProfitLoss() {
  const now = today();
  const { values, set } = usePeriod(["start", "end"], { start: yearStart(now), end: now });
  const report = useGet<Report>("/api/accounting/financial-statements/profit-and-loss/", { start: values.start!, end: values.end! });
  const data = report.data;
  const query = `?from=${values.start}&to=${values.end}`;
  return (
    <section className="report statement">
      <StatementHead title="Profit and loss">
        <label className="inline">From <input type="date" value={values.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="inline">To <input type="date" value={values.end} onChange={(e) => set("end", e.target.value)} /></label>
      </StatementHead>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : !data ? <div className="loading">Adding up…</div> : (
        <>
          <Section title="Income" rows={data.income} total={data.income_total} query={query} />
          <Section title="Expenses" rows={data.expenses} total={data.expense_total} query={query} />
          <div className="tiles"><div className={`tile${isNegative(data.net_profit) ? " bad" : ""}`}><span>{isNegative(data.net_profit) ? "Loss" : "Profit"}</span><strong>{money(data.net_profit)}</strong></div></div>
        </>
      )}
    </section>
  );
}
