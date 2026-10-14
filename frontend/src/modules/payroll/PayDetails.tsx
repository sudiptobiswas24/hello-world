import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "employee_number", label: "Employee", width: "9rem" },
  { key: "account_holder", label: "Account holder" },
  { key: "account_number", label: "Account", width: "12rem" },
  { key: "ifsc", label: "IFSC", width: "9rem" },
  { key: "pan", label: "PAN", width: "9rem" },
];

export default function PayDetails() {
  return (
    <ListView<Row>
      title="Bank and PAN"
      noun={["record", "records"]}
      endpoint="/api/hr/pay-details/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/pay-details/${row.id}`}
      searchHint="Employee number or holder"
      create={{ href: "/payroll/pay-details/new", permission: "hr.add_employeepaydetails" }}
    />
  );
}
