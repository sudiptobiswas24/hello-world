import { RecordScreen } from "../../views/RecordScreen";
import { REASON, SERVES, SHIFT } from "./refs";

/**
 * One stoppage. Booked where it happened; a breakdown is handed to
 * maintenance from here as a repair job, which rests on it.
 */
export default function StoppageForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/downtime/"
      back="/plant/stoppages"
      backLabel="Stoppages"
      newTitle="New stoppage"
      heading={(row) => String(row.number)}
      permissions={{ add: "manufacturing.add_downtime", change: "manufacturing.change_downtime",
        delete: "manufacturing.delete_downtime" }}
      fields={[
        ...SERVES,
        { key: "shift_date", label: "Day", kind: "date" },
        { key: "shift", label: "Shift", kind: "ref", ref: SHIFT },
        { key: "reason", label: "Why", kind: "ref", ref: REASON },
        { key: "minutes", label: "Minutes stopped", kind: "decimal", places: 2 },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      actions={[{
        label: "Raise a repair", path: "breakdown", permission: "manufacturing.add_maintenancejob",
        url: () => "/api/manufacturing/maintenance-jobs/breakdown/",
        body: (values, record) => ({ downtime: record.id, fault: values.fault }),
        fields: [{ key: "fault", label: "What failed", kind: "text" }],
        done: "Repair raised", then: (job) => `/plant/jobs/${job.id}`,
      }]}
    />
  );
}
