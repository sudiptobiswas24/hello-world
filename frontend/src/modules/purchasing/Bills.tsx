import { ListView, type Column } from "../../views/ListView";

interface Bill {
  id: number;
  number: string;
  vendor_name: string;
  reference: string;
  bill_date: string;
  due_date: string | null;
  total: string;
  amount_due: string;
  posted: boolean;
  debits: number | null;
  is_prepayment: boolean;
  settlement_status: string;
}

export function billState(row: Bill): { label: string; tone: string } {
  if (!row.posted) return { label: "Draft", tone: "draft" };
  if (row.debits) return { label: "Debit note", tone: "info" };
  if (row.is_prepayment) return { label: "Advance", tone: "info" };
  if (row.settlement_status === "paid") return { label: "Paid", tone: "done" };
  if (row.settlement_status === "partial") return { label: "Part paid", tone: "warn" };
  return { label: "Unpaid", tone: "open" };
}

const columns: Column<Bill>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "vendor_name", label: "Vendor" },
  { key: "reference", label: "Their invoice", width: "10rem" },
  { key: "bill_date", label: "Date", kind: "date", sort: "bill_date", width: "8rem" },
  { key: "due_date", label: "Due", kind: "date", sort: "due_date", width: "8rem" },
  { key: "total", label: "Total", kind: "money", width: "9rem" },
  { key: "amount_due", label: "Due now", kind: "money", width: "9rem" },
  {
    key: "state", label: "State", width: "7rem",
    render: (row) => {
      const { label, tone } = billState(row);
      return <span className={`pill pill-${tone}`}>{label}</span>;
    },
  },
];

export default function Bills() {
  return (
    <ListView<Bill>
      title="Bills"
      noun={["bill", "bills"]}
      endpoint="/api/purchasing/bills/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/bills/${row.id}`}
      create={{ href: "/purchasing/bills/new", permission: "purchasing.add_bill" }}
      searchHint="Number, vendor, their invoice number, order"
      facets={[
        { label: "Drafts", params: { posted: "false" } },
        { label: "To pay", params: { open: "true" } },
        { label: "Debit notes", params: { debits__isnull: "false" } },
      ]}
    />
  );
}
