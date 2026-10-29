import { Link } from "react-router";

import { date } from "../../lib/format";
import { ReportView } from "../../views/ReportView";

interface Row {
  id: number; number: string; lot_code: string; item_label: string; failed: string;
  inspected_on: string; due_on: string; overdue: boolean; escalated_on: string | null;
  [key: string]: unknown;
}

/**
 * Failures under an advisory plan, each holding its batch until the
 * Quality Manager decides: oldest first, the overdue marked. Each opens
 * the inspection, where the three decisions are.
 */
export default function Awaiting() {
  return (
    <ReportView<Row[], Row>
      title="Awaiting decision"
      endpoint="/api/quality/inspections/awaiting/"
      params={[]}
      rows={(data) => data}
      columns={[
        { key: "number", label: "Inspection", width: "10rem",
          render: (row) => <Link to={`/quality/inspections/${row.id}`}>{row.number}</Link> },
        { key: "lot_code", label: "Batch", width: "9rem" },
        { key: "item_label", label: "Item" },
        { key: "failed", label: "Failed on" },
        { key: "inspected_on", label: "Inspected", width: "8rem", render: (row) => date(row.inspected_on) },
        { key: "due_on", label: "Decision due", width: "8rem", render: (row) => date(row.due_on) },
        { key: "overdue", label: "", width: "9rem",
          render: (row) => (row.overdue
            ? <span className="pill pill-overdue">{row.escalated_on ? "Overdue, escalated" : "Overdue"}</span>
            : "") },
      ]}
      empty="Nothing waits for your decision."
    />
  );
}
