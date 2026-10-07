import { money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: string };
interface Report { rows: Row[]; total: string; statement_expenses: string; foots: boolean }

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-30" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];

/**
 * Posted expenses by the centre that incurred them. What carries no
 * centre is shown as unallocated, and the total foots to the profit and
 * loss account's expenses for the same days — or says that it does not.
 */
export default function CostsByCentre() {
  return (
    <ReportView<Report, Row>
      title="Costs by centre"
      endpoint="/api/accounting/cost-centres/report/"
      params={PARAMS}
      rows={(data) => data.rows.map((row) => ({ ...row, id: String(row.code || "unallocated") }))}
      above={(data) => (
        <p className="muted">
          Total {money(data.total)} · profit and loss expenses {money(data.statement_expenses)} ·{" "}
          {data.foots ? "foots" : "DOES NOT FOOT"}
        </p>
      )}
      columns={[
        { key: "code", label: "Centre", width: "9rem" },
        { key: "name", label: "Name" },
        { key: "amount", label: "Expenses", kind: "money", width: "12rem" },
      ]}
      empty="Nothing was spent in these days."
    />
  );
}
