import { RecordScreen } from "../../views/RecordScreen";
import { SPEED_BASES, UOM } from "./refs";

/**
 * A bank of like machines: what it can do, the hours it works, and what
 * an hour of it costs. The rates are what every run on it is costed at.
 */
export default function WorkCentreForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/work-centres/"
      back="/making/work-centres"
      backLabel="Work centres"
      newTitle="New work centre"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_workcentre", change: "manufacturing.change_workcentre",
        delete: "manufacturing.delete_workcentre" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "description", label: "Description", kind: "textarea" },
        { key: "speed_basis", label: "Speed worked out from", kind: "choice", choices: SPEED_BASES, initial: "stated" },
        { key: "capacity_per_hour", label: "Capacity an hour", kind: "decimal", hint: "For a stated rate" },
        { key: "capacity_uom", label: "Capacity unit", kind: "ref", ref: UOM },
        { key: "tape_ends", label: "Tape ends", kind: "integer", hint: "Tape line" },
        { key: "line_speed_m_per_min", label: "Line speed m/min", kind: "decimal", hint: "Tape line or web" },
        { key: "loom_rpm", label: "Loom rpm", kind: "decimal", hint: "Circular loom" },
        { key: "shuttles", label: "Shuttles", kind: "integer", hint: "Circular loom" },
        { key: "efficiency_percent", label: "Efficiency %", kind: "decimal", initial: "100" },
        { key: "available_hours_per_day", label: "Hours a day", kind: "decimal", initial: "24" },
        { key: "working_days", label: "Working days", initial: "1234567", hint: "1 Monday to 7 Sunday" },
        { key: "holiday_region", label: "Holiday calendar" },
        { key: "operators_per_machine", label: "Operators a machine", kind: "decimal" },
        { key: "machine_rate_per_hour", label: "Machine an hour", kind: "money", initial: "0" },
        { key: "labour_rate_per_hour", label: "Labour an hour", kind: "money", initial: "0" },
        { key: "overhead_rate_per_hour", label: "Overhead an hour", kind: "money", initial: "0" },
        { key: "conversion_rate_per_hour", label: "Conversion an hour", readOnly: true },
        { key: "standard_kwh_per_hour", label: "Standard kWh an hour", kind: "decimal", places: 3 },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Machines", permission: "manufacturing.view_machine",
        endpoint: "/api/manufacturing/machines/", query: (record) => ({ work_centre: record.id }),
        href: (row) => `/making/machines/${row.id}`,
        columns: [
          { key: "code", label: "Machine", width: "9rem" },
          { key: "name", label: "Name" },
          { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
        ],
      }]}
    />
  );
}
