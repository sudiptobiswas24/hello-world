import { RecordScreen } from "../../views/RecordScreen";
import { UOM, VENDOR, WORK_CENTRE } from "./refs";

/** One loom, extruder or press in its bank, and what it can take. Empty: the bank's figure. */
export default function MachineForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/machines/"
      back="/making/machines"
      backLabel="Machines"
      newTitle="New machine"
      heading={(row) => `${String(row.code)} · ${String(row.name || row.work_centre_name)}`}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_machine", change: "manufacturing.change_machine", delete: "manufacturing.delete_machine" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "work_centre", label: "Bank", kind: "ref", ref: WORK_CENTRE },
        { key: "capacity_per_hour", label: "Capacity an hour", kind: "decimal", hint: "Empty: the bank's" },
        { key: "capacity_uom", label: "Capacity unit", kind: "ref", ref: UOM },
        { key: "available_hours_per_day", label: "Hours a day", kind: "decimal", hint: "Empty: the bank's" },
        { key: "working_days", label: "Working days", hint: "Days of the week, 1 Monday to 7 Sunday, e.g. 123456; empty: the bank's" },
        { key: "min_width_cm", label: "Narrowest cm", kind: "decimal" },
        { key: "max_width_cm", label: "Widest cm", kind: "decimal" },
        { key: "min_length_cm", label: "Shortest cm", kind: "decimal" },
        { key: "max_length_cm", label: "Longest cm", kind: "decimal" },
        { key: "max_colours", label: "Most colours", kind: "integer" },
        { key: "inserts_liner", label: "Inserts a liner", kind: "bool" },
        { key: "contractor", label: "At a contractor", kind: "pick", pick: VENDOR, hint: "A machine of a job worker's, planned like our own" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "notes", label: "Notes", wide: true },
      ]}
    />
  );
}
