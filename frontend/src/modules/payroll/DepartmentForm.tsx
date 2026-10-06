import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const EMPLOYEE: FieldDef["pick"] = { endpoint: "/api/hr/employees/", permission: "hr.view_employee", label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}` };

/** A department, its manager and the cost centre its pay is charged to. */
export default function DepartmentForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/departments/"
      back="/payroll/departments"
      backLabel="Departments"
      newTitle="New department"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "hr.add_department", change: "hr.change_department", delete: "hr.delete_department" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "manager", label: "Manager", kind: "pick", pick: EMPLOYEE },
        { key: "cost_centre", label: "Cost centre", kind: "pick", pick: ACCOUNT, hint: "Where this department's payroll is charged" },
      ]}
    />
  );
}
