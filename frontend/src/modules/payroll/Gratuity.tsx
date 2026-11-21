import { money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { employee: number };
interface Report {
  note?: string; rows: Row[]; total: string; provided: string | null; short: string | null }

const PARAMS: ParamDef[] = [
  { key: "as_of", label: "As of", kind: "date", required: true, initial: "today" },
  { key: "components", label: "On", kind: "choice", required: true, initial: "BASIC,DA",
    choices: [["BASIC,DA", "Basic and DA"], ["BASIC", "Basic"]] },
  { key: "provision", label: "Provision", kind: "ref",
    ref: { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.id) } },
];

/** Gratuity owed to each person in service, fifteen days' wages a counted year, set against the provision. */
export default function Gratuity() {
  return (
    <ReportView<Report, Row>
      title="Gratuity"
      endpoint="/api/hr/gratuity/"
      params={PARAMS}
      rows={(data) => data.rows.map((row) => ({ ...row, id: row.employee }))}
      above={(data) => (
        <>
          {data.note && <p className="form-error" role="alert">{data.note}</p>}
          {data.provided !== null
            ? <p>Owed {money(data.total)} against {money(data.provided)} provided: {money(data.short)} short.</p>
            : <p>Owed {money(data.total)} in all.</p>}
        </>
      )}
      columns={[
        { key: "number", label: "No.", width: "6rem" },
        { key: "name", label: "Name" },
        { key: "hired", label: "Joined", kind: "date", width: "8rem" },
        { key: "years", label: "Years", width: "5rem" },
        { key: "wages", label: "Monthly wages", kind: "money", width: "9rem" },
        { key: "owed", label: "Owed", kind: "money", width: "9rem" },
        { key: "payable", label: "", width: "8rem", render: (row) => (row.payable ? "Payable" : "Under 5 years") },
      ]}
      empty="Nobody in service on that day."
    />
  );
}
