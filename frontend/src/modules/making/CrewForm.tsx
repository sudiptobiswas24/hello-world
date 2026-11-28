import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE, SHIFT, WORK_CENTRE } from "./refs";

/** Who is on which bank's shift, from when: what the planner's manning check counts. */
export default function CrewForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/crew-assignments/"
      back="/making/crews"
      backLabel="Crews"
      newTitle="New crew assignment"
      heading={(row) => `${String(row.employee_name)}, ${String(row.work_centre_name)} ${String(row.shift_name)}`}
      permissions={{ add: "manufacturing.add_crewassignment", change: "manufacturing.change_crewassignment",
        delete: "manufacturing.delete_crewassignment" }}
      fields={[
        { key: "employee", label: "Who", kind: "pick", pick: EMPLOYEE, show: (row) => String(row.employee_name) },
        { key: "work_centre", label: "Bank", kind: "ref", ref: WORK_CENTRE },
        { key: "shift", label: "Shift", kind: "ref", ref: SHIFT },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date", hint: "Empty: until changed" },
      ]}
    />
  );
}
