import { ListView, type Column } from "../../views/ListView";

interface Delivery {
  id: number;
  number: string;
  order_number: string;
  customer_name: string;
  delivery_date: string;
  posted: boolean;
  reverses: number | null;
}

const columns: Column<Delivery>[] = [
  { key: "number", label: "Number", sort: "number", render: (row) => row.number || "Draft", width: "11rem" },
  { key: "customer_name", label: "Customer" },
  { key: "order_number", label: "Order", width: "11rem" },
  { key: "delivery_date", label: "Date", kind: "date", sort: "delivery_date", width: "8rem" },
  {
    key: "state",
    label: "State",
    width: "8rem",
    render: (row) => row.reverses
      ? <span className="pill pill-info">Return</span>
      : <span className={`pill pill-${row.posted ? "done" : "draft"}`}>{row.posted ? "Shipped" : "Draft"}</span>,
  },
];

export default function Deliveries() {
  return (
    <ListView<Delivery>
      title="Deliveries"
      noun={["delivery", "deliveries"]}
      endpoint="/api/sales/deliveries/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/deliveries/${row.id}`}
      searchHint="Number, order, customer, reference"
      facets={[
        { label: "To ship", params: { posted: "false" } },
        { label: "Shipped", params: { posted: "true" } },
        { label: "Returns", params: { reverses__isnull: "false" } },
      ]}
    />
  );
}
