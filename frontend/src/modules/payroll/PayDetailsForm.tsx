import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const EMPLOYEE: FieldDef["pick"] = { endpoint: "/api/hr/employees/", permission: "hr.view_employee", label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}` };

/** What the bank file and the returns need of a person, read only by payroll. */
export default function PayDetailsForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/pay-details/"
      back="/payroll/pay-details"
      backLabel="Bank and PAN"
      newTitle="New bank and PAN details"
      heading={(row) => `Bank and PAN · ${String(row.employee_number ?? "")}`}
      permissions={{ add: "hr.add_employeepaydetails", change: "hr.change_employeepaydetails", delete: "hr.delete_employeepaydetails" }}
      fields={[
        { key: "employee", label: "Employee", kind: "pick", pick: EMPLOYEE },
        { key: "account_holder", label: "Account holder" },
        { key: "account_number", label: "Account number", hint: "Nine to eighteen digits" },
        { key: "ifsc", label: "IFSC", hint: "Four letters, a 0, six letters or digits" },
        { key: "pan", label: "PAN" },
        { key: "date_of_birth", label: "Date of birth", kind: "date" },
        { key: "scheduled_employment", label: "Scheduled employment", hint: "As the State's minimum wage notification names their work" },
        { key: "wage_zone", label: "Minimum wage zone", hint: "Blank where the notification has none" },
        { key: "skill", label: "Skill", kind: "choice", choices: [["", "Not set"], ["unskilled", "Unskilled"], ["semi_skilled", "Semi-skilled"], ["skilled", "Skilled"], ["highly_skilled", "Highly skilled"]] },
      ]}
    />
  );
}
