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

const FIXED = { receipt: { direction: "receipt" }, disbursement: { direction: "disbursement" } };

/** Money in or out: one list, the direction decides which. */
export function PaymentList({ direction, title, base, noun, party }: {
  direction: "receipt" | "disbursement"; title: string; base: string; noun: [string, string]; party: string;
}) {
  return (
    <ListView<Payment>
      title={title}
      noun={noun}
      endpoint="/api/accounting/payments/"
      fixed={FIXED[direction]}
      columns={direction === "receipt" ? columns : columns.map((column) => column.key === "party_name" ? { ...column, label: "To" } : column)}
      rowKey={(row) => row.id}
      create={{ href: `${base}/new`, permission: "accounting.add_payment" }}
      rowHref={(row) => `${base}/${row.id}`}
      searchHint={`Number, ${party}, reference`}
      facets={[{ label: "Drafts", params: { posted: "false" } }, { label: "Posted", params: { posted: "true" } }]}
    />
  );
}
