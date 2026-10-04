import { ListView, type Column } from "../../views/ListView";

interface Order {
  id: number;
  number: string;
  customer_name: string;
  order_date: string;
  status: string;
  total: string;
  delivery_status: string;
  invoice_status: string;
}

const PROGRESS: Record<string, string> = { none: "Not started", partial: "Part", full: "Done" };

const columns: Column<Order>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "customer_name", label: "Customer" },
  { key: "order_date", label: "Date", kind: "date", sort: "order_date", width: "8rem" },
  { key: "total", label: "Total", kind: "money", width: "9rem" },
  { key: "delivery_status", label: "Dispatched", width: "8rem", render: (row) => PROGRESS[row.delivery_status] ?? row.delivery_status },
  { key: "invoice_status", label: "Invoiced", width: "8rem", render: (row) => PROGRESS[row.invoice_status] ?? row.invoice_status },
  { key: "status", label: "State", kind: "status", width: "8rem" },
];

export default function Orders() {
  return (
    <ListView<Order>
      title="Sales orders"
      noun={["order", "orders"]}
      endpoint="/api/sales/sales-orders/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Number, customer, reference"
      facets={[
        { label: "Drafts", params: { status: "draft" } },
        { label: "Confirmed", params: { status: "confirmed" } },
        { label: "Cancelled", params: { status: "cancelled" } },
      ]}
    />
  );
}
