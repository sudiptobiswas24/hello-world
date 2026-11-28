import { ListView, type Column } from "../../views/ListView";

interface Plan { id: number; name: string; item_label: string; is_mandatory: boolean; is_active: boolean; lines_count: number; [key: string]: unknown }

const columns: Column<Plan>[] = [
  { key: "name", label: "Plan", sort: "name" },
  { key: "item_label", label: "Item" },
  { key: "lines_count", label: "Checks", width: "7rem", kind: "quantity", render: (row) => String(row.lines_count) },
  { key: "is_mandatory", label: "Holds the batch", width: "9rem", render: (row) => (row.is_mandatory ? "Yes" : "No") },
];

/** What is tested on each item, and the limits it must meet. */
export default function Plans() {
  return (
    <ListView<Plan>
      title="Inspection plans"
      noun={["plan", "plans"]}
      endpoint="/api/quality/plans/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/plans/${row.id}`}
      searchHint="Plan, item"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/quality/plans/new", permission: "quality.add_inspectionplan" }}
    />
  );
}
