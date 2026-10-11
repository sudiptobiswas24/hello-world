import { asPercent, minutesAsHours } from "../../lib/decimal";
import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { operator: string; name: string; work_centre: string; minutes: string; ideal_minutes: string;
  performance: string | null; bookings: number; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "work_centre", label: "Work centre", kind: "ref",
    ref: { endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre",
      label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.code) } },
  { key: "start", label: "From", kind: "date", initial: "-30" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/**
 * What each person's booked hours produced against what the step's rate
 * says they could have. Attribution, not appraisal: every row names the
 * bank, and an old loom reads worse than a new one whoever runs it.
 */
export default function OperatorYield() {
  const columns: Column<Row>[] = [
    { key: "operator", label: "Who", width: "8rem" },
    { key: "name", label: "Name" },
    { key: "work_centre", label: "Bank", width: "8rem" },
    { key: "bookings", label: "Bookings", width: "7rem", kind: "quantity", render: (row) => String(row.bookings) },
    { key: "hours", label: "Hours", width: "7rem", kind: "quantity", render: (row) => minutesAsHours(row.minutes) },
    { key: "ideal", label: "At the rate", width: "8rem", kind: "quantity", render: (row) => minutesAsHours(row.ideal_minutes) },
    { key: "performance", label: "Performance", width: "8rem",
      render: (row) => asPercent(row.performance) },
  ];
  return (
    <ReportView<Row[], Row>
      title="Operator yield"
      endpoint="/api/manufacturing/operator-yield/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="No time booked in these days."
    />
  );
}
