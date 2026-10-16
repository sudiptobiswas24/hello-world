import { minutesAsHours } from "../../lib/decimal";
import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Stoppage {
  id: number;
  number: string;
  shift_date: string;
  serves: string;
  reason_name: string;
  is_planned: boolean;
  minutes: string;
  shift_name: string;
  work_order_number: string;
  [key: string]: unknown;
}

const columns: Column<Stoppage>[] = [
  { key: "number", label: "Number", width: "9rem", sort: "number" },
  { key: "shift_date", label: "Day", width: "8rem", sort: "shift_date", render: (row) => date(row.shift_date) },
  { key: "shift_name", label: "Shift", width: "7rem" },
  { key: "serves", label: "Where" },
  { key: "reason_name", label: "Why", render: (row) => `${row.reason_name}${row.is_planned ? " (planned)" : ""}` },
  { key: "minutes", label: "Hours", width: "6rem", kind: "quantity", sort: "minutes", render: (row) => minutesAsHours(row.minutes) },
  { key: "work_order_number", label: "Run", width: "9rem" },
];

/** Every stoppage booked, at the machine or here. */
export default function Stoppages() {
  return (
    <ListView<Stoppage>
      title="Stoppages"
      noun={["stoppage", "stoppages"]}
      endpoint="/api/manufacturing/downtime/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/stoppages/${row.id}`}
      searchHint="Number, notes"
      facets={[
        { label: "Unplanned", params: { reason__is_planned: "false" } },
        { label: "Planned", params: { reason__is_planned: "true" } },
      ]}
      create={{ href: "/plant/stoppages/new", permission: "manufacturing.add_downtime" }}
    />
  );
}
