import { RecordScreen } from "../../views/RecordScreen";

/** A kind of leave and how many days of it a year brings. */
export default function LeavePolicyForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/leave-policies/"
      back="/payroll/leave-policies"
      backLabel="Leave policies"
      newTitle="New leave policy"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "hr.add_leavepolicy", change: "hr.change_leavepolicy", delete: "hr.delete_leavepolicy" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "leave_type", label: "Leave type", kind: "choice", choices: [["vacation", "Vacation"], ["sick", "Sick"], ["unpaid", "Unpaid"], ["other", "Other"]] },
        { key: "annual_days", label: "Annual days", kind: "decimal", places: 2, initial: "0", hint: "Working days a full year grants" },
      ]}
    />
  );
}
