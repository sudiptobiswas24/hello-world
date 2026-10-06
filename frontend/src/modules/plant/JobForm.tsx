import { money } from "../../lib/format";
import { minutesAsHours } from "../../lib/decimal";
import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE, ITEM, SERVES, WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";

/**
 * One job: a service that fell due or a repair on a stoppage. Fitters'
 * time and spares are booked to it while it is open; spares leave the
 * store at their cost and come back by returning the issue.
 */
export default function JobForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/maintenance-jobs/"
      back="/plant/jobs"
      backLabel="Maintenance jobs"
      newTitle="New maintenance job"
      heading={(row) => `${String(row.title)}, ${String(row.serves)}`}
      state={(row) => ({ label: String(row.status), tone: row.status === "open" ? "open" : row.status === "done" ? "done" : "draft" })}
      permissions={{ add: "manufacturing.add_maintenancejob", change: "manufacturing.change_maintenancejob" }}
      editable={open}
      fields={[
        ...SERVES.map((field) => ({ ...field, createOnly: true })),
        { key: "due_on", label: "Due", kind: "date" },
        { key: "planned_minutes", label: "Planned minutes", kind: "decimal", places: 2 },
        { key: "fault", label: "Fault", kind: "text", hint: "For a repair: what failed" },
        { key: "technician", label: "Fitter", kind: "pick", pick: EMPLOYEE, show: (row) => String(row.technician_name || "—") },
        { key: "notes", label: "Notes", kind: "text" },
        { key: "schedule_name", label: "From schedule", readOnly: true },
        { key: "done_on", label: "Done", kind: "date", readOnly: true },
        { key: "actual_minutes", label: "Machine stopped (min)", kind: "decimal", readOnly: true },
        { key: "cause", label: "Cause", readOnly: true },
        { key: "action_taken", label: "What was done", readOnly: true },
        { key: "labour_minutes", label: "Fitters' time", readOnly: true, show: (row) => `${minutesAsHours(row.labour_minutes as string)} h` },
        { key: "spares_value", label: "Spares", readOnly: true, show: (row) => money(row.spares_value as string) },
      ]}
      actions={[
        { label: "Complete", path: "complete", permission: "manufacturing.change_maintenancejob", when: open, primary: true,
          done: "Completed", fields: [
            { key: "on_date", label: "Done on", kind: "date" },
            { key: "minutes", label: "Minutes the machine stood", kind: "decimal", places: 2, hint: "For a service; a repair's is its stoppage" },
            { key: "cause", label: "Cause", kind: "text", hint: "For a repair" },
            { key: "action", label: "What was done", kind: "text", hint: "For a repair" },
          ] },
        { label: "Book a fitter's time", path: "labour", permission: "manufacturing.change_maintenancejob", when: open,
          done: "Time booked", fields: [
            { key: "technician", label: "Fitter", kind: "pick", pick: EMPLOYEE },
            { key: "worked_on", label: "Day", kind: "date" },
            { key: "minutes", label: "Minutes", kind: "decimal", places: 2 },
          ] },
        { label: "Issue spares", path: "spares", permission: "manufacturing.change_maintenancejob", when: open,
          done: "Spares issued", fields: (row) => [
            { key: "warehouse", label: "From store", kind: "ref", ref: WAREHOUSE },
            { key: "item", label: "Spare", kind: "pick", pick: ITEM },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            // Where on the machine it goes, so its life can be read off the gaps between placements.
            ...(row.machine ? [{ key: "position", label: "Goes to", kind: "ref" as const, hint: "The position on the machine, if it has one",
              ref: { endpoint: "/api/manufacturing/machine-positions/", permission: "manufacturing.view_machineposition",
                query: { machine: String(row.machine) }, label: (position: Row) => `${String(position.code)} ${String(position.name ?? "")}`.trim() } }] : []),
          ],
          body: (values) => ({ warehouse: values.warehouse,
            lines: [{ item: values.item, quantity: values.quantity, ...(values.position ? { position: values.position } : {}) }] }) },
        { label: "Cancel", path: "cancel", permission: "manufacturing.change_maintenancejob", when: open, danger: true,
          done: "Cancelled", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[
        { title: "Fitters' time", permission: "manufacturing.view_maintenancejob", endpoint: "", query: () => ({}),
          rows: (record) => (record.labour as Row[]) ?? [],
          columns: [
            { key: "technician_name", label: "Fitter" },
            { key: "worked_on", label: "Day", kind: "date" },
            { key: "minutes", label: "Minutes", kind: "quantity" },
          ] },
        { title: "Spares", permission: "manufacturing.view_maintenancejob", endpoint: "", query: () => ({}),
          rows: (record) => (record.spares as Row[]) ?? [],
          columns: [
            { key: "adjustment", label: "Issue" },
            { key: "items", label: "What" },
            { key: "value", label: "Value", kind: "money" },
            { key: "standing", label: "State", render: (row) => (row.standing ? "Issued" : "Returned") },
          ],
          rowAction: { label: "Return", permission: "manufacturing.change_maintenancejob",
            when: (row) => Boolean(row.standing),
            url: (row, record) => `/api/manufacturing/maintenance-jobs/${record.id}/spares/${row.id}/return/`,
            done: "Returned to the store", confirm: "Return these spares to the store?" } },
      ]}
    />
  );
}
