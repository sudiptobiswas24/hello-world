import { RecordScreen } from "../../views/RecordScreen";
import { INSTRUMENT } from "./refs";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => !row.posted;

/**
 * An instrument checked against a standard. Found out of tolerance, the
 * inspections it took since it was last good are listed as suspect.
 */
export default function CalibrationForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/calibrations/"
      back="/quality/calibrations"
      backLabel="Calibrations"
      newTitle="New calibration"
      heading={(row) => String(row.number || "Calibration")}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" } : row.posted ? { label: "Posted", tone: "done" } : { label: "Draft", tone: "open" })}
      permissions={{ add: "quality.add_calibration", change: "quality.change_calibration" }}
      editable={draft}
      fields={[
        { key: "instrument", label: "Instrument", kind: "ref", ref: INSTRUMENT, createOnly: true },
        { key: "calibrated_on", label: "Calibrated on", kind: "date" },
        { key: "due_on", label: "Next due", kind: "date" },
        { key: "result", label: "Found", kind: "choice", choices: [["pass", "In tolerance"], ["adjusted", "Out of tolerance, adjusted"], ["fail", "Out of tolerance, taken out of service"]] },
        { key: "performed_by", label: "By", kind: "text" },
        { key: "certificate_reference", label: "Certificate" },
        { key: "traceable_to", label: "Traceable to" },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "quality.change_calibration", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "quality.change_calibration", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Inspections now in doubt", permission: "quality.view_inspection",
        endpoint: "", query: () => ({}), rows: (record) => (record.suspects as Row[]) ?? [],
        columns: [
          { key: "inspection", label: "Inspection" },
          { key: "lot", label: "Batch" },
          { key: "inspected_on", label: "On", kind: "date" },
        ],
      }]}
    />
  );
}
