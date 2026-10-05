import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE, METER, SHIFT } from "./refs";

/** One reading: what the dial said at a shift's end. A wrong one is voided and read again. */
export default function ReadingForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/meter-readings/"
      back="/plant/readings"
      backLabel="Meter readings"
      newTitle="New meter reading"
      heading={(row) => `${String(row.meter_code)}, ${String(row.shift_date)}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_meterreading" }}
      afterCreate={() => "/plant/readings/new"}
      fields={[
        { key: "meter", label: "Meter", kind: "ref", ref: METER, createOnly: true },
        { key: "shift_date", label: "Day", kind: "date", createOnly: true },
        { key: "shift", label: "Shift", kind: "ref", ref: SHIFT, createOnly: true, hint: "Empty for a reading once a day" },
        { key: "reading", label: "Reading", kind: "decimal", places: 3, createOnly: true, hint: "What the dial says, not what was used" },
        { key: "read_by", label: "Read by", kind: "pick", pick: EMPLOYEE, createOnly: true, show: (row) => String(row.read_by_name || "—") },
        { key: "voided_reason", label: "Why voided", readOnly: true },
      ]}
      actions={[{
        label: "Void", path: "void", permission: "manufacturing.change_meterreading", danger: true,
        when: (row) => !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", kind: "text" }],
      }]}
    />
  );
}
