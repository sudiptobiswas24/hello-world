import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "employee", label: "Employee", width: "8rem" },
  { key: "kind", label: "Kind", width: "8rem" },
  { key: "amount", label: "Given", kind: "money", width: "9rem" },
  { key: "instalment", label: "A run", kind: "money", width: "9rem" },
  { key: "recovered", label: "Recovered", kind: "money", width: "9rem" },
  { key: "balance", label: "Left", kind: "money", width: "9rem" },
  { key: "recover_from", label: "From", kind: "date", width: "8rem" },
];

export default function Advances() {
  return (
    <ListView<Row>
      title="Advances and loans"
      noun={["advance", "advances"]}
      endpoint="/api/hr/salary-advances/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/advances/${row.id}`}
      create={{ href: "/payroll/advances/new", permission: "hr.add_salaryadvance" }}
    />
  );
}
