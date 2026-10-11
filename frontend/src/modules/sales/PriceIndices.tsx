import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
];

export default function PriceIndices() {
  return (
    <ListView<Row>
      title="Price indices"
      noun={["price index", "price indices"]}
      endpoint="/api/sales/price-indices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/price-indices/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/sales/price-indices/new", permission: "sales.add_priceindex" }}
    />
  );
}
