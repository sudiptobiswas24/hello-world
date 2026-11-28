import { ListView, type Column } from "../../views/ListView";

interface Run {
  id: number;
  number: string;
  name: string;
  period_start: string;
  period_end: string;
  pay_date: string;
  status: string;
  gross: string;
  net: string;
  unpaid_net: string;
}

const columns: Column<Run>[] = [
  { key: "number", label: "Run", sort: "number", width: "10rem", render: (row) => row.number || "Draft" },
  { key: "name", label: "Name" },
  { key: "period_start", label: "From", kind: "date", sort: "period_start", width: "8rem" },
  { key: "period_end", label: "To", kind: "date", width: "8rem" },
  { key: "pay_date", label: "Paid on", kind: "date", sort: "pay_date", width: "8rem" },
  { key: "gross", label: "Gross", kind: "money", width: "9rem" },
  { key: "net", label: "Net", kind: "money", width: "9rem" },
  { key: "unpaid_net", label: "Still to pay", kind: "money", width: "9rem" },
  { key: "status", label: "State", kind: "status", width: "7rem" },
];

export default function PayRuns() {
  return (
    <ListView<Run>
      title="Pay runs"
      noun={["pay run", "pay runs"]}
      endpoint="/api/hr/pay-runs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/runs/${row.id}`}
      create={{ href: "/payroll/runs/new", permission: "hr.add_payrun" }}
      searchHint="Number or name"
    />
  );
}
