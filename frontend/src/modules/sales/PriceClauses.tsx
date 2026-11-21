import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "line_label", label: "Order line" },
  { key: "index_code", label: "Index", width: "8rem" },
  { key: "base_value", label: "Base", kind: "quantity", width: "8rem" },
  { key: "polymer_kg_per_unit", label: "Polymer kg a unit", kind: "quantity", width: "10rem" },
  { key: "pass_through_percent", label: "Passed on %", kind: "quantity", width: "8rem" },
];

export default function PriceClauses() {
  return (
    <ListView<Row>
      title="Price variation clauses"
      noun={["clause", "clauses"]}
      endpoint="/api/sales/price-clauses/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/price-clauses/${row.id}`}
      searchHint="Order, customer or index"
      create={{ href: "/sales/price-clauses/new", permission: "sales.add_pricevariationclause" }}
    />
  );
}
