import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { StatementHead, usePeriod } from "../accounts/Statement";

interface Row {
  run: string;
  employee: string;
  name: string;
  wages?: string;
  tax?: string;
  employee_share?: string;
  employer_share?: string;
  state: string;
  from: string;
}

interface Register {
  kind: string;
  month: string;
  rows: Row[];
}

/** A month's professional tax or welfare fund register, read off what each posted run kept. */
export default function StatutoryRegisters() {
  const { values, set } = usePeriod(["kind", "month"], { kind: "pt", month: today().slice(0, 7) });
  const report = useGet<Register>("/api/hr/statutory-registers/", { kind: values.kind!, month: values.month! });
  const pt = values.kind === "pt";
  return (
    <section className="report">
      <StatementHead title="Statutory registers">
        <label className="inline">Register <select value={values.kind} onChange={(e) => set("kind", e.target.value)}>
          <option value="pt">Professional tax</option>
          <option value="lwf">Labour welfare fund</option>
        </select></label>
        <label className="inline">Month <input type="month" value={values.month} onChange={(e) => set("month", e.target.value)} /></label>
      </StatementHead>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th scope="col">Run</th><th scope="col">Employee</th>
              {pt ? <><th scope="col" className="k-money">Wages</th><th scope="col" className="k-money">Tax</th></>
                : <><th scope="col" className="k-money">Employee</th><th scope="col" className="k-money">Employer</th></>}
              <th scope="col">Schedule</th>
            </tr></thead>
            <tbody>
              {(report.data?.rows ?? []).map((row) => (
                <tr key={`${row.run}-${row.employee}`}>
                  <td>{row.run}</td>
                  <td><strong>{row.employee}</strong> {row.name}</td>
                  {pt ? <><td className="k-money">{money(row.wages ?? "")}</td><td className="k-money">{money(row.tax ?? "")}</td></>
                    : <><td className="k-money">{money(row.employee_share ?? "")}</td><td className="k-money">{money(row.employer_share ?? "")}</td></>}
                  <td>{[row.state, row.from ? `from ${row.from}` : ""].filter(Boolean).join(" ") || "as entered"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.data && report.data.rows.length === 0 && <div className="empty"><p>Nothing in this register for the month.</p></div>}
        </div>
      )}
    </section>
  );
}
