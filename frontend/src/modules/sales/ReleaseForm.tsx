import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** One agency release: the batches it passed for this customer. A wrong one is voided, and those batches are held again. */
export default function ReleaseForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/third-party-releases/"
      back="/sales/releases"
      backLabel="Third-party releases"
      newTitle="Release"
      heading={(row) => `${String(row.number)} · ${String(row.customer_name)}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" } : { label: "Posted", tone: "done" })}
      permissions={{}}
      fields={[
        { key: "customer_name", label: "Customer", readOnly: true },
        { key: "order_number", label: "Order", readOnly: true },
        { key: "agency_name", label: "Agency", readOnly: true },
        { key: "inspector", label: "Inspector", readOnly: true },
        { key: "their_reference", label: "Their certificate", readOnly: true },
        { key: "inspected_on", label: "Inspected", kind: "date", readOnly: true },
        { key: "voided_reason", label: "Voided because", readOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: "sales.change_thirdpartyrelease", danger: true,
          when: (row) => !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Batches", permission: "sales.view_thirdpartyrelease", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "lot_code", label: "Batch" },
          { key: "quantity_offered", label: "Offered", kind: "quantity", width: "9rem" },
          { key: "quantity_released", label: "Released", kind: "quantity", width: "9rem" },
          { key: "remarks", label: "Remarks" },
        ],
      }]}
    />
  );
}
