import { ListView, type Column } from "../../views/ListView";

interface Order {
  id: number;
  number: string;
  vendor_name: string;
  order_date: string;
  status: string;
  total: string;
  receipt_status: string;
  bill_status: string;
}

const PROGRESS: Record<string, string> = { none: "Not started", partial: "Part", full: "Done" };

const columns: Column<Order>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "vendor_name", label: "Vendor" },
  { key: "order_date", label: "Date", kind: "date", sort: "order_date", width: "8rem" },
  { key: "total", label: "Total", kind: "money", width: "9rem" },
  { key: "receipt_status", label: "Received", width: "8rem", render: (row) => PROGRESS[row.receipt_status] ?? row.receipt_status },
  { key: "bill_status", label: "Billed", width: "8rem", render: (row) => PROGRESS[row.bill_status] ?? row.bill_status },
  { key: "status", label: "State", kind: "status", width: "8rem" },
];

export default function Orders() {
  return (
    <ListView<Order>
      title="Purchase orders"
      noun={["order", "orders"]}
      endpoint="/api/purchasing/purchase-orders/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/orders/${row.id}`}
      create={{ href: "/purchasing/orders/new", permission: "purchasing.add_purchaseorder" }}
      searchHint="Number, vendor, reference"
      facets={[
        { label: "Drafts", params: { status: "draft" } },
        { label: "To receive", params: { to_receive: "true" } },
        { label: "Confirmed", params: { status: "confirmed" } },
        { label: "Cancelled", params: { status: "cancelled" } },
      ]}
    />
  );
}
