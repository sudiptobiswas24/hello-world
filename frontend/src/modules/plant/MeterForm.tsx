import { RecordScreen } from "../../views/RecordScreen";
import { SERVES } from "./refs";

/**
 * A meter on one machine or one work centre. Once read, what the readings
 * were taken against cannot change: retire it and install another.
 */
export default function MeterForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/energy-meters/"
      back="/plant/meters"
      backLabel="Energy meters"
      newTitle="New energy meter"
      heading={(row) => `${String(row.code)}, ${String(row.serves)}`}
      state={(row) => (row.retired_on ? { label: "Retired", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_energymeter", change: "manufacturing.change_energymeter",
        delete: "manufacturing.delete_energymeter" }}
      fields={[
        { key: "code", label: "Meter" },
        { key: "machine", label: "Machine", kind: "ref", ref: SERVES[1]!.ref, hint: "One machine, or else a whole work centre" },
        { key: "work_centre", label: "Work centre", kind: "ref", ref: SERVES[0]!.ref },
        { key: "multiplier", label: "Multiplier", kind: "decimal", initial: "1", hint: "kWh per unit the dial moves, from the meter card" },
        { key: "installed_on", label: "Installed", kind: "date" },
        { key: "initial_reading", label: "Reading when installed", kind: "decimal", places: 3, initial: "0" },
        { key: "retired_on", label: "Retired", kind: "date", existingOnly: true },
      ]}
      panels={[{
        title: "Readings", permission: "manufacturing.view_meterreading",
        endpoint: "/api/manufacturing/meter-readings/", query: (record) => ({ meter: record.id, ordering: "-shift_date" }),
        href: (row) => `/plant/readings/${row.id}`,
        columns: [
          { key: "shift_date", label: "Day", kind: "date" },
          { key: "shift_name", label: "Shift" },
          { key: "reading", label: "Reading", kind: "quantity" },
          { key: "voided_at", label: "", render: (row) => (row.voided_at ? "Voided" : "") },
        ],
      }]}
    />
  );
}
