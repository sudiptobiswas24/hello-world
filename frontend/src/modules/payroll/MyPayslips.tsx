import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "run_number", label: "Run", width: "10rem" },
  { key: "period_start", label: "From", kind: "date", width: "8rem" },
  { key: "period_end", label: "To", kind: "date", width: "8rem" },
  { key: "gross", label: "Earned", kind: "money", width: "9rem" },
  { key: "deductions", label: "Deducted", kind: "money", width: "9rem" },
  { key: "net", label: "Net pay", kind: "money", width: "9rem" },
  {
    key: "pdf", label: "Payslip", width: "8rem",
    render: (row) => <a href={`/api/hr/my-payslips/${row.id}/pdf/`} target="_blank" rel="noreferrer">PDF</a>,
  },
];

/** The signed-in employee's own payslips from posted runs: the server serves nobody else's. */
export default function MyPayslips() {
  return (
    <ListView<Row>
      title="My payslips"
      noun={["payslip", "payslips"]}
      endpoint="/api/hr/my-payslips/"
      columns={columns}
      rowKey={(row) => row.id}
    />
  );
}
