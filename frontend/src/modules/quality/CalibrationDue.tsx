import { date } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { instrument: string; status: string; due_on: string | null; [key: string]: unknown }

const PARAMS: ParamDef[] = [{ key: "within", label: "Due within (days)", kind: "choice", required: true, initial: "30",
  choices: [["7", "7"], ["30", "30"], ["60", "60"], ["90", "90"]] }];

/** Instruments past their calibration, or coming up to it: a reading on one is worth nothing. */
export default function CalibrationDue() {
  return (
    <ReportView<Row[], Row>
      title="Calibration due"
      endpoint="/api/quality/instruments/due/"
      params={PARAMS}
      rows={(data) => data}
      columns={[
        { key: "instrument", label: "Instrument" },
        { key: "status", label: "", width: "10rem", kind: "status" },
        { key: "due_on", label: "Due", width: "9rem", render: (row) => date(row.due_on) || "Never calibrated" },
      ]}
      empty="Every instrument is in date."
    />
  );
}
