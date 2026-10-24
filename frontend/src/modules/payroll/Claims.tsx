import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", sort: "number", width: "9rem", render: (row) => String(row.number || "Draft") },
  { key: "claim_date", label: "Date", kind: "date", sort: "claim_date", width: "8rem" },
  { key: "employee_name", label: "Who" },
  { key: "purpose", label: "For" },
  { key: "total", label: "Amount", kind: "money", width: "9rem" },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

/** Money spent for the company and asked back: one's own, one's reports', or everyone's to who keeps the records. */
export default function Claims() {
  return (
    <ListView<Row>
      title="Expense claims"
      noun={["claim", "claims"]}
      endpoint="/api/hr/expense-claims/"
      columns={columns}
      rowHref={(row) => `/payroll/claims/${row.id}`}
      searchHint="Number, purpose or who"
      facets={[{ label: "To decide", params: { status: "submitted" } }, { label: "To pay", params: { status: "approved" } }]}
      create={{ href: "/payroll/claims/new", permission: "hr.add_expenseclaim" }}
    />
  );
}
