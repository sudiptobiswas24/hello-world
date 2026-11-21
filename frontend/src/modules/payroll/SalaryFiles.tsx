import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "File", width: "10rem" },
  { key: "narration", label: "Narration" },
  { key: "total", label: "Total", kind: "money", width: "10rem" },
  { key: "paid_at", label: "Paid", kind: "date", width: "8rem" },
  {
    key: "csv", label: "For the bank", width: "8rem",
    render: (row) => <a href={`/api/hr/salary-files/${row.id}/csv/`}>CSV</a>,
  },
];

/** Each run's bank file: written from the run, paid by one payment out, a transfer sent back unpaying one slip. */
export default function SalaryFiles() {
  return (
    <ListView<Row>
      title="Bank files"
      noun={["file", "files"]}
      endpoint="/api/hr/salary-files/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/salary-files/${row.id}`}
    />
  );
}
