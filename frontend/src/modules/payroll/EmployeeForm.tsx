import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const DEPARTMENT: FieldDef["ref"] = { endpoint: "/api/hr/departments/", permission: "hr.view_department", label: (row: Row) => String(row.name || row.code) };
const COMPONENT: FieldDef["ref"] = { endpoint: "/api/hr/pay-components/", permission: "hr.view_paycomponent", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const EMPLOYEE: FieldDef["pick"] = { endpoint: "/api/hr/employees/", permission: "hr.view_employee", label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}` };

/** Someone the company employs: their department, manager and working days, and the login that is theirs. */
export default function EmployeeForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/employees/"
      back="/payroll/employees"
      backLabel="Employees"
      newTitle="New employee"
      heading={(row) => String(row.employee_number ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.employment_status === "terminated" ? { label: "Left", tone: "draft" } : null)}
      permissions={{ add: "hr.add_employee", change: "hr.change_employee", delete: "hr.delete_employee" }}
      fields={[
        { key: "name", label: "Name", readOnly: true, existingOnly: true },
        { key: "new_name", label: "Name", newOnly: true, hint: "Who is being taken on" },
        { key: "party", label: "Or an existing party", kind: "pick", pick: PARTY, newOnly: true,
          hint: "Someone already known: a vendor's man joining the payroll" },
        { key: "employee_number", label: "Employee number" },
        { key: "department", label: "Department", kind: "ref", ref: DEPARTMENT },
        { key: "manager", label: "Manager", kind: "pick", pick: EMPLOYEE },
        { key: "job_title", label: "Job title" },
        { key: "hire_date", label: "Hire date", kind: "date" },
        { key: "termination_date", label: "Termination date", kind: "date" },
        { key: "employment_status", label: "Employment status", kind: "choice", choices: [["active", "Active"], ["on_leave", "On Leave"], ["terminated", "Terminated"]], initial: "active" },
        { key: "working_days", label: "Working days", initial: "12345", hint: "ISO weekday numbers this person works" },
        { key: "holiday_region", label: "Holiday region", hint: "Which public holidays apply" },
        { key: "paid_by_attendance", label: "Paid by attendance", kind: "bool", initial: false, hint: "A day-rated worker: the pay run waits until every working day of theirs is on the register" },
        { key: "uan", label: "UAN", hint: "Provident fund, twelve digits: the ECR names them by it" },
        { key: "esi_number", label: "ESI number", hint: "The IP number, ten digits: the ESI file names them by it" },
      ]}
      panels={[{
        // What they are paid, component by component, from a date: a
        // change is a new row from its date, never an edit of the old one.
        title: "Pay", permission: "hr.view_employeecompensation", endpoint: "/api/hr/compensation/",
        query: (employee) => ({ employee: String(employee.id) }),
        columns: [
          { key: "component_name", label: "Component" },
          { key: "amount", label: "Amount", kind: "money", width: "10rem" },
          { key: "effective_from", label: "From", kind: "date", width: "8rem" },
          { key: "effective_to", label: "To", kind: "date", width: "8rem" },
        ],
        adder: { label: "Add pay", permission: "hr.add_employeecompensation", url: () => "/api/hr/compensation/",
          fields: [
            { key: "component", label: "Component", kind: "ref", ref: COMPONENT },
            { key: "amount", label: "Amount", kind: "money" },
            { key: "effective_from", label: "From", kind: "date" },
            { key: "note", label: "Note" },
          ],
          body: (values, employee) => ({ ...values, employee: employee.id }) },
      }]}
    />
  );
}
