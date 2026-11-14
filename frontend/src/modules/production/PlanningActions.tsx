import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "action", label: "Do", kind: "status", width: "8rem" },
  { key: "item_sku", label: "Item", width: "10rem" },
  { key: "item_name", label: "" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
  { key: "scheduled_on", label: "Now on", kind: "date", width: "8rem" },
  { key: "wanted_on", label: "Wanted on", kind: "date", width: "8rem" },
  { key: "sentence", label: "Why" },
];

/** What the last planning runs say to do about orders already open: expedite, defer, cancel, raise. */
export default function PlanningActions() {
  return (
    <ListView<Row>
      title="What the plan says to do"
      noun={["action", "actions"]}
      endpoint="/api/planning/actions/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Item SKU or name"
      facets={[
        { label: "Expedite", params: { action: "expedite" } },
        { label: "Defer", params: { action: "defer" } },
        { label: "Cancel", params: { action: "cancel" } },
      ]}
    />
  );
}
