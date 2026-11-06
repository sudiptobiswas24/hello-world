import { RecordScreen, type PanelDef } from "../../views/RecordScreen";
import { ITEM } from "../plant/refs";
import { UOM, VENDOR, WORK_CENTRE } from "./refs";

/** Where on the machine a spare goes, which spare, and whether the machine stands without it. */
const POSITIONS_PANEL: PanelDef = {
  title: "Positions", permission: "manufacturing.view_machineposition",
  endpoint: "/api/manufacturing/machine-positions/", query: (machine) => ({ machine: String(machine.id) }),
  columns: [
    { key: "code", label: "Position", width: "8rem" },
    { key: "name", label: "" },
    { key: "spare_item_sku", label: "Spare", width: "10rem" },
    { key: "is_critical", label: "", width: "7rem", render: (row) => (row.is_critical ? "critical" : "") },
  ],
  adder: { label: "Add a position", permission: "manufacturing.add_machineposition", url: () => "/api/manufacturing/machine-positions/",
    fields: [
      { key: "code", label: "Position", hint: "As the fitter says it: BRG-DS, SCREEN" },
      { key: "name", label: "Name" },
      { key: "spare_item", label: "Spare it takes", kind: "pick", pick: ITEM },
      { key: "is_critical", label: "The machine stands without it", kind: "bool", initial: false },
    ],
    body: (values, machine) => ({ ...values, machine: machine.id }) },
  remover: { permission: "manufacturing.delete_machineposition", url: (row) => `/api/manufacturing/machine-positions/${row.id}/` },
};

/** One loom, extruder or press in its bank, and what it can take. Empty: the bank's figure. */
export default function MachineForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/machines/"
      extras="manufacturing.machine"
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
      panels={[POSITIONS_PANEL]}
    />
  );
}
