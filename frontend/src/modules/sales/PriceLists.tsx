import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "is_default", label: "Default" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function PriceLists() {
  return (
    <ListView<Row>
      title="Price lists"
      noun={["price list", "price lists"]}
      endpoint="/api/sales/price-lists/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/price-lists/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/sales/price-lists/new", permission: "sales.add_pricelist" }}
    />
  );
}
