import { Link } from "react-router";

import { useAccess } from "../../auth/me";
import { ReportView, type ParamDef } from "../../views/ReportView";
import { CLAIM_REASONS } from "./claimReasons";

type Row = Record<string, unknown> & { note: number };

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-90" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];
const named = (value: unknown) => CLAIM_REASONS.find(([key]) => key === value)?.[1] ?? String(value ?? "");

/** Money given back on customers' claims: torn bags, short weight, a rate in dispute. What quality cost. */
export default function Claims() {
  const { can } = useAccess();
  return (
    <ReportView<Row[], Row>
      title="Claims"
      endpoint="/api/sales/invoices/claims/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.note }))}
      above={() => (can("sales.post_invoice") ? <Link className="btn primary" to="/sales/claims/new">New claim</Link> : null)}
      columns={[
        { key: "date", label: "Date", kind: "date", width: "8rem" },
        { key: "number", label: "Credit note", width: "10rem" },
        { key: "customer", label: "Customer" },
        { key: "invoice", label: "On", width: "10rem" },
        { key: "reason", label: "For", width: "13rem", render: (row) => named(row.reason) },
        { key: "complaint", label: "Complaint", width: "8rem" },
        { key: "net", label: "Before tax", kind: "money", width: "9rem" },
        { key: "total", label: "With tax", kind: "money", width: "9rem" },
      ]}
      empty="No claim was credited in these dates."
    />
  );
}
