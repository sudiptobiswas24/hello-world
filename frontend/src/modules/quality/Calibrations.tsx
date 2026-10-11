import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Calibration { id: number; number: string; instrument_code: string; calibrated_on: string; due_on: string; result: string; posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Calibration>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "instrument_code", label: "Instrument" },
  { key: "calibrated_on", label: "On", width: "8rem", sort: "calibrated_on", render: (row) => date(row.calibrated_on) },
  { key: "due_on", label: "Next due", width: "8rem", render: (row) => date(row.due_on) },
  { key: "result", label: "Found", width: "8rem", kind: "status" },
  { key: "state", label: "State", width: "7rem", render: (row) => (row.voided_at ? "Voided" : row.posted ? "Posted" : "Draft") },
];

export default function Calibrations() {
  return (
    <ListView<Calibration>
      title="Calibrations"
      noun={["calibration", "calibrations"]}
      endpoint="/api/quality/calibrations/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/calibrations/${row.id}`}
      searchHint="Number, instrument"
      create={{ href: "/quality/calibrations/new", permission: "quality.add_calibration" }}
    />
  );
}
