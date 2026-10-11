import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** What went in and what came out. Posted when recorded; a wrong one is voided, which puts the batches back. */
export default function RebatchForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/rebatches/"
      back="/production/rebatches"
      backLabel="Rebatches"
      newTitle="Rebatch"
      heading={(row) => `${String(row.number)} · ${String(row.item_label)}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" } : { label: "Posted", tone: "done" })}
      permissions={{}}
      fields={[
        { key: "item_label", label: "Item", readOnly: true },
        { key: "warehouse_name", label: "Store", readOnly: true },
        { key: "rebatched_on", label: "Date", kind: "date", readOnly: true },
        { key: "reason", label: "Why", readOnly: true },
        { key: "voided_reason", label: "Voided because", readOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: "manufacturing.change_rebatch", danger: true,
          when: (row) => !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Batches", permission: "manufacturing.view_rebatch", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "side", label: "", width: "6rem", kind: "status" },
          { key: "lot_code", label: "Batch" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
        ],
      }]}
    />
  );
}
