import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "period", label: "For the month of", kind: "date", width: "10rem" },
  { key: "account_name", label: "Owed into" },
  { key: "payment_number", label: "Paid by", width: "10rem" },
  { key: "amount", label: "Amount", kind: "money", width: "10rem" },
];

/** PF, ESI and tax paid over against a month's payroll: what clears the liability the pay runs booked. */
export default function Remittances() {
  return (
    <ListView<Row>
      title="Statutory remittances"
      noun={["remittance", "remittances"]}
      endpoint="/api/hr/statutory-remittances/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/remittances/${row.id}`}
      searchHint="Account or payment"
      create={{ href: "/payroll/remittances/new", permission: "hr.add_statutoryremittance" }}
    />
  );
}
