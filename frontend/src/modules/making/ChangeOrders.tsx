import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "9rem", render: (row) => String(row.number || "Draft") },
  { key: "item_label", label: "Item" },
  { key: "supersedes_label", label: "Replaces", render: (row) => `version ${String(row.supersedes_label).split("version ")[1] ?? ""}` },
  { key: "effective_from", label: "From", kind: "date", sort: "effective_from", width: "9rem" },
  { key: "reason", label: "Why" },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

/** A recipe in use changes by a change order: a new version with a first day, raised from the bill of materials. */
export default function ChangeOrders() {
  return (
    <ListView<Row>
      title="BOM change orders"
      noun={["change order", "change orders"]}
      endpoint="/api/manufacturing/bom-change-orders/"
      columns={columns}
      rowHref={(row) => `/making/change-orders/${row.id}`}
      searchHint="Number, item or reason"
      facets={[{ label: "Open", params: { status: "draft" } }, { label: "Applied", params: { status: "applied" } }]}
    />
  );
}
