import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Asset", width: "9rem" },
  { key: "name", label: "Name" },
  { key: "category_name", label: "Category", width: "11rem" },
  { key: "cost", label: "Cost", kind: "money", width: "10rem" },
  { key: "net_book_value", label: "Book value", kind: "money", width: "10rem" },
  { key: "status", label: "State", kind: "status", width: "8rem" },
];

export default function Assets() {
  return (
    <ListView<Row>
      title="Fixed assets"
      noun={["asset", "assets"]}
      endpoint="/api/assets/assets/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/assets/${row.id}`}
      searchHint="Number or name"
      facets={[{ label: "In service", params: { status: "in_service" } }, { label: "Drafts", params: { status: "draft" } }]}
      create={{ href: "/accounts/assets/new", permission: "assets.add_fixedasset" }}
      actions={[
        { label: "Run this month's depreciation", permission: "accounting.post_journalentry", path: "/api/assets/assets/depreciate-all/",
          confirm: "Charge depreciation on every asset in service, through the last month end?",
          done: (result) => `${String(((result as { charged?: unknown[] }).charged ?? []).length)} charges posted` },
      ]}
    />
  );
}
