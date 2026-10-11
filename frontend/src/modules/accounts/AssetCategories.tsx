import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "default_life_months", label: "Life (months)", kind: "quantity" },
  { key: "method", label: "Method" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function AssetCategories() {
  return (
    <ListView<Row>
      title="Asset categories"
      noun={["asset category", "asset categories"]}
      endpoint="/api/assets/categories/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/categories/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/accounts/categories/new", permission: "assets.add_assetcategory" }}
    />
  );
}
