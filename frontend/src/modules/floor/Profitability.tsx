import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row {
  line: number; item: string; quoted: { cost: string; price: string } | null; actual: { direct: string } | null;
  made: string | null; shipped: string | null; revenue: string | null; cost_of_shipped: string | null;
  margin: string | null; margin_percent: string | null; [key: string]: unknown;
}

const PARAMS: ParamDef[] = [
  { key: "order", label: "Order", kind: "ref", required: true,
    ref: { endpoint: "/api/sales/sales-orders/", label: (row) => `${String(row.number)} · ${String(row.customer_name)}`,
      value: (row) => String(row.id), permission: "sales.view_salesorder" } },
];

/** An order's lines as quoted against what making and shipping them cost: what the quote assumed, and what the order earned. */
export default function Profitability() {
  const columns: Column<Row>[] = [
    { key: "item", label: "Item", width: "10rem" },
    { key: "quoted", label: "Quoted cost", kind: "money", width: "9rem", render: (row) => row.quoted?.cost ?? "" },
    { key: "actual", label: "Actual cost", kind: "money", width: "9rem", render: (row) => row.actual?.direct ?? "" },
    { key: "shipped", label: "Shipped", kind: "quantity", width: "8rem" },
    { key: "revenue", label: "Revenue", kind: "money", width: "10rem" },
    { key: "cost_of_shipped", label: "Cost of it", kind: "money", width: "10rem" },
    { key: "margin", label: "Margin", kind: "money", width: "9rem" },
    { key: "margin_percent", label: "%", kind: "quantity", width: "6rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Order profitability"
      endpoint="/api/manufacturing/order-profitability/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="Nothing made or shipped on this order yet."
    />
  );
}
