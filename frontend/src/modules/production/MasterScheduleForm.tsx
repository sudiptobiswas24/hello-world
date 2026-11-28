import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => !row.committed_at && !row.withdrawn_at;

/** One week's build-ahead: checked against capacity, then committed as a run, or withdrawn. */
export default function MasterScheduleForm() {
  return (
    <RecordScreen
      endpoint="/api/planning/master-schedule/"
      back="/production/master-schedule"
      backLabel="Master schedule"
      newTitle="New master schedule week"
      heading={(row) => `${String(row.item_label)}, week of ${String(row.week_of)}`}
      state={(row) => (row.withdrawn_at ? { label: "Withdrawn", tone: "draft" } : row.committed_at ? { label: "Committed", tone: "done" } : { label: "Draft", tone: "open" })}
      permissions={{ add: "planning.add_masterscheduleentry", change: "planning.change_masterscheduleentry",
        delete: "planning.delete_masterscheduleentry" }}
      editable={draft}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: { endpoint: "/api/inventory/items/", permission: "inventory.view_item",
          label: (row) => `${String(row.sku)} · ${String(row.name)}` }, show: (row) => String(row.item_label) },
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: { endpoint: "/api/inventory/warehouses/",
          permission: "inventory.view_warehouse", label: (row) => String(row.name || row.code) } },
        { key: "week_of", label: "Week of", kind: "date" },
        { key: "quantity", label: "Quantity", kind: "decimal" },
        { key: "reason", label: "Why", kind: "text", wide: true },
        { key: "withdrawn_reason", label: "Withdrawn because", readOnly: true },
      ]}
      actions={[
        { label: "Commit", path: "commit", permission: "planning.change_masterscheduleentry", when: draft, primary: true,
          done: "Committed: the run is raised",
          fields: [{ key: "accept_overload", label: "If it overloads a machine, why go ahead", kind: "text", hint: "Empty refuses an overload" }] },
        { label: "Withdraw", path: "withdraw", permission: "planning.change_masterscheduleentry", danger: true,
          when: (row) => Boolean(row.committed_at) && !row.withdrawn_at, done: "Withdrawn",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
    />
  );
}
