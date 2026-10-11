import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** One bale: the bags in it by batch, and whether it is still on the shelf. Packed at the station, not here. */
export default function BaleForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/bales/"
      back="/production/bales"
      backLabel="Bales"
      newTitle="Bale"
      heading={(row) => `${String(row.number)} · ${String(row.item)}`}
      state={(row) => ({ label: String(row.status), tone: row.delivery ? "done" : "open" })}
      permissions={{}}
      fields={[
        { key: "item", label: "Of", readOnly: true },
        { key: "packed_on", label: "Packed", kind: "date", readOnly: true },
        { key: "packed_by", label: "By", readOnly: true },
        { key: "warehouse", label: "Store", readOnly: true },
        { key: "bags", label: "Bags", readOnly: true },
        { key: "nominal_kg", label: "Kg by the bags", readOnly: true },
        { key: "gross_kg", label: "Kg on the scale", readOnly: true },
        { key: "delivery", label: "Shipped on", readOnly: true },
      ]}
      actions={[
        { label: "Take off the delivery", path: "unload", permission: "manufacturing.change_bale", danger: true,
          when: (row) => Boolean(row.delivery), done: "Taken off" },
        { label: "Break open", path: "break", permission: "manufacturing.change_bale", danger: true,
          when: (row) => !row.delivery, done: "Broken open", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Bags in it", permission: "manufacturing.view_bale", endpoint: "", query: () => ({}),
        rows: (record) => ((record.lines as Record<string, unknown>[]) ?? []).map((line, index) => ({ id: index, ...line }) as Row),
        columns: [
          { key: "lot", label: "Batch" },
          { key: "bags", label: "Bags", kind: "quantity", width: "8rem" },
        ],
      }]}
    />
  );
}
