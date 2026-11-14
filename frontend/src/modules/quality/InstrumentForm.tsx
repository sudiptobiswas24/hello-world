import { RecordScreen } from "../../views/RecordScreen";

export default function InstrumentForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/instruments/"
      back="/quality/instruments"
      backLabel="Instruments"
      newTitle="New instrument"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      permissions={{ add: "quality.add_instrument", change: "quality.change_instrument" }}
      fields={[
        { key: "code", label: "Code", createOnly: true },
        { key: "name", label: "Instrument" },
        { key: "serial_number", label: "Serial number" },
        { key: "location", label: "Where" },
        { key: "interval_days", label: "Calibrated every (days)", kind: "integer" },
        { key: "range_low", label: "Reads from", kind: "decimal" },
        { key: "range_high", label: "Reads up to", kind: "decimal" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "is_weighbridge", label: "Weighbridge", kind: "bool", readOnly: true, existingOnly: true,
          show: (row) => (row.is_weighbridge ? "Yes: it weighs vehicles at the gate" : "No") },
      ]}
      actions={[
        { label: "Mark as the weighbridge", path: "weighbridge", permission: "quality.set_weighbridge",
          when: (row) => !row.is_weighbridge, done: "Marked", body: () => ({ is_weighbridge: true }) },
        { label: "Not a weighbridge", path: "weighbridge", permission: "quality.set_weighbridge",
          when: (row) => Boolean(row.is_weighbridge), done: "Unmarked", body: () => ({ is_weighbridge: false }) },
      ]}
      panels={[{
        title: "Calibrations", permission: "quality.view_calibration",
        endpoint: "/api/quality/calibrations/", query: (record) => ({ instrument: record.id }),
        href: (row) => `/quality/calibrations/${row.id}`,
        columns: [
          { key: "number", label: "Number" },
          { key: "calibrated_on", label: "On", kind: "date" },
          { key: "due_on", label: "Next due", kind: "date" },
          { key: "result", label: "Found", kind: "status" },
        ],
      }]}
    />
  );
}
