import { ListView, type Column } from "../../views/ListView";

interface Payment {
  id: number;
  number: string;
  party_name: string;
  payment_date: string;
  amount: string;
  reference: string;
  posted: boolean;
  voided: boolean;
}

const columns: Column<Payment>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "party_name", label: "From" },
  { key: "payment_date", label: "Date", kind: "date", sort: "payment_date", width: "8rem" },
  { key: "reference", label: "Reference", width: "12rem" },
  { key: "amount", label: "Amount", kind: "money", sort: "amount", width: "10rem" },
  {
    key: "state", label: "State", width: "7rem",
    render: (row) => <span className={`pill pill-${row.voided ? "cancelled" : row.posted ? "done" : "draft"}`}>{row.voided ? "Void" : row.posted ? "Posted" : "Draft"}</span>,
  },
];

const FIXED = { direction: "receipt" };

export default function Receipts() {
  return (
    <ListView<Payment>
      title="Money received"
      noun={["receipt", "receipts"]}
      endpoint="/api/accounting/payments/"
      fixed={FIXED}
      columns={columns}
      rowKey={(row) => row.id}
      create={{ href: "/sales/receipts/new", permission: "accounting.add_payment" }}
      rowHref={(row) => `/sales/receipts/${row.id}`}
      searchHint="Number, customer, reference"
      facets={[{ label: "Drafts", params: { posted: "false" } }, { label: "Posted", params: { posted: "true" } }]}
    />
  );
}
