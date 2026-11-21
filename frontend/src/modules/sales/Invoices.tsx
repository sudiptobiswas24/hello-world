import { ListView, type Column } from "../../views/ListView";

interface Invoice {
  id: number;
  number: string;
  customer_name: string;
  invoice_date: string;
  due_date: string | null;
  total: string;
  amount_due: string;
  posted: boolean;
  credits: number | null;
  is_down_payment: boolean;
  settlement_status: string;
}

function state(row: Invoice): { label: string; tone: string } {
  if (!row.posted) return { label: "Draft", tone: "draft" };
  if (row.credits) return { label: "Credit note", tone: "info" };
  if (row.is_down_payment) return { label: "Advance", tone: "info" };
  switch (row.settlement_status) {
    case "paid":
      return { label: "Paid", tone: "done" };
    case "partial":
      return { label: "Part paid", tone: "warn" };
    default:
      return { label: "Unpaid", tone: "open" };
  }
}

const columns: Column<Invoice>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "customer_name", label: "Customer" },
  { key: "invoice_date", label: "Date", kind: "date", sort: "invoice_date", width: "8rem" },
  { key: "due_date", label: "Due", kind: "date", sort: "due_date", width: "8rem" },
  { key: "total", label: "Total", kind: "money", width: "9rem" },
  { key: "amount_due", label: "Due now", kind: "money", width: "9rem" },
  {
    key: "state",
    label: "State",
    width: "7rem",
    render: (row) => {
      const { label, tone } = state(row);
      return <span className={`pill pill-${tone}`}>{label}</span>;
    },
  },
];

export default function Invoices() {
  return (
    <ListView<Invoice>
      title="Invoices"
      noun={["invoice", "invoices"]}
      endpoint="/api/sales/invoices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/invoices/${row.id}`}
      create={{ href: "/sales/invoices/new", permission: "sales.add_invoice" }}
      searchHint="Number, customer, reference, order"
      facets={[
        { label: "Drafts", params: { posted: "false" } },
        { label: "Posted", params: { posted: "true" } },
        { label: "Credit notes", params: { credits__isnull: "false" } },
        { label: "Advances", params: { is_down_payment: "true" } },
      ]}
    />
  );
}
