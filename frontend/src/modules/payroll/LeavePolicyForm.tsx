import { RecordScreen } from "../../views/RecordScreen";

/**
 * A kind of leave and how many days of it a year brings: read here, kept in the admin (LeavePolicyViewSet takes
 * no write). The screen offered New and Save to whoever held the model's permissions, and each answered 405 (O602).
 */
export default function LeavePolicyForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/leave-policies/"
      back="/payroll/leave-policies"
      backLabel="Leave policies"
      newTitle="New leave policy"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{}}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "leave_type", label: "Leave type", kind: "choice", choices: [["vacation", "Vacation"], ["sick", "Sick"], ["unpaid", "Unpaid"], ["other", "Other"]] },
        { key: "annual_days", label: "Annual days", kind: "decimal", places: 2, initial: "0", hint: "Working days a full year grants. A change applies from next January: this year keeps the days it had" },
      ]}
    />
  );
}
