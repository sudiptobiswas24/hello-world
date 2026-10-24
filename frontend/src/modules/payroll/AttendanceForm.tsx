import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE } from "../quality/refs";

const STATUSES: [string, string][] = [["present", "Present"], ["half_day", "Half a day"], ["absent", "Absent"]];
const TONES: Record<string, string> = { present: "done", half_day: "open", absent: "cancelled" };

/**
 * One person's day on the register. How late they were is worked out
 * from the shift's start where the shift and the time in are known. A
 * day inside a posted pay run is what that run paid on and stays as it
 * was until the run is voided.
 */
export default function AttendanceForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/attendance/"
      back="/payroll/attendance"
      backLabel="Attendance"
      newTitle="Mark a day"
      heading={(row) => `${String(row.employee_name ?? "")} · ${String(row.on ?? "")}`}
      state={(row) => (row.status ? { label: String(row.status).replace("_", " "), tone: TONES[String(row.status)] ?? "draft" } : null)}
      permissions={{ add: "hr.add_attendanceday", change: "hr.change_attendanceday", delete: "hr.delete_attendanceday" }}
      fields={[
        { key: "employee", label: "Who", kind: "pick", pick: EMPLOYEE, createOnly: true,
          show: (row) => `${String(row.employee_number ?? "")} · ${String(row.employee_name ?? "")}` },
        { key: "on", label: "Day", kind: "date", createOnly: true },
        { key: "status", label: "Came in", kind: "choice", choices: STATUSES, initial: "present" },
        { key: "shift", label: "Shift", hint: "The shift's code, so how late they were can be worked out" },
        { key: "time_in", label: "In", kind: "time" },
        { key: "time_out", label: "Out", kind: "time" },
        { key: "late_minutes", label: "Late by, minutes", kind: "integer", readOnly: true, existingOnly: true },
        { key: "overtime_hours", label: "Overtime hours", kind: "decimal", places: 2, initial: "0", hint: "Agreed for the day, paid at the overtime rate" },
        { key: "source", label: "From", readOnly: true, existingOnly: true },
        { key: "note", label: "Note", wide: true },
      ]}
    />
  );
}
