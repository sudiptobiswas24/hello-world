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
