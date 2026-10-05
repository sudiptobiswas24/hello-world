import { date } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";
import { SERVES } from "./refs";

/**
 * A routine: every N days, every N running hours, or both, whichever
 * comes first. Running hours are counted from the time booked on the
 * machine, never typed.
 */
export default function ScheduleForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/maintenance-schedules/"
      back="/plant/schedules"
      backLabel="Maintenance schedules"
      newTitle="New maintenance schedule"
      heading={(row) => `${String(row.name)}, ${String(row.serves)}`}
      state={(row) => (row.is_due ? { label: "Due", tone: "warn" } : row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_maintenanceschedule", change: "manufacturing.change_maintenanceschedule",
        delete: "manufacturing.delete_maintenanceschedule" }}
      fields={[
        { key: "name", label: "Work", hint: "Screen change, shuttle and bearing check" },
        ...SERVES,
        { key: "every_days", label: "Every (days)", kind: "integer" },
        { key: "every_run_hours", label: "Every (running hours)", kind: "decimal", places: 2 },
        { key: "duration_minutes", label: "Takes (minutes)", kind: "decimal", places: 2, hint: "Booked against the machine when a job is raised" },
        { key: "last_done_on", label: "Last done", kind: "date" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "due_on", label: "Due by the calendar", readOnly: true, show: (row) => date(row.due_on as string) || "—" },
        { key: "hours_remaining", label: "Running hours left", readOnly: true },
      ]}
      actions={[{
        label: "Raise the job", path: "raise_job", permission: "manufacturing.add_maintenancejob",
        when: (row) => Boolean(row.is_active), done: "Job raised",
        fields: [{ key: "due_on", label: "For", kind: "date" }],
        then: (job) => `/plant/jobs/${job.id}`,
      }]}
      panels={[{
        title: "Jobs", permission: "manufacturing.view_maintenancejob",
        endpoint: "/api/manufacturing/maintenance-jobs/", query: (record) => ({ schedule: record.id }),
        href: (row) => `/plant/jobs/${row.id}`,
        columns: [
          { key: "due_on", label: "Due", kind: "date" },
          { key: "done_on", label: "Done", kind: "date" },
          { key: "status", label: "State", kind: "status" },
        ],
      }]}
    />
  );
}
