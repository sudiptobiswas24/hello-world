import { ListView, type Column } from "../../views/ListView";

interface Receipt {
  id: number;
  number: string;
  order_number: string;
  vendor_name: string;
  receipt_date: string;
  posted: boolean;
  reverses: number | null;
}

const columns: Column<Receipt>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "vendor_name", label: "Vendor" },
  { key: "order_number", label: "Order", width: "11rem" },
  { key: "receipt_date", label: "Date", kind: "date", sort: "receipt_date", width: "8rem" },
  {
    key: "state", label: "State", width: "8rem",
    render: (row) => row.reverses
      ? <span className="pill pill-info">Sent back</span>
      : <span className={`pill pill-${row.posted ? "done" : "draft"}`}>{row.posted ? "Received" : "Draft"}</span>,
  },
];

export default function GoodsReceipts() {
  return (
    <ListView<Receipt>
      title="Goods in"
      noun={["receipt", "receipts"]}
      endpoint="/api/purchasing/goods-receipts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/goods-in/${row.id}`}
      searchHint="Number, order, vendor, reference"
      facets={[
        { label: "To post", params: { posted: "false" } },
        { label: "Received", params: { posted: "true", reverses__isnull: "true" } },
        { label: "Sent back", params: { reverses__isnull: "false" } },
      ]}
    />
  );
}
