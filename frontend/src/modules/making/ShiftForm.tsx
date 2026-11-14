import { RecordScreen } from "../../views/RecordScreen";

/** A shift by when it starts and how long it runs; one past midnight belongs to the day it started. */
export default function ShiftForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/shifts/"
      back="/making/shifts"
      backLabel="Shifts"
      newTitle="New shift"
      heading={(row) => String(row.name || row.code)}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_shift", change: "manufacturing.change_shift", delete: "manufacturing.delete_shift" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "starts_at", label: "Starts", kind: "time" },
        { key: "hours", label: "Hours", kind: "decimal", initial: "8" },
        { key: "ends_at", label: "Ends", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
