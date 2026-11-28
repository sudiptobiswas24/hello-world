import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => row.status === "draft";

const CONTRACT = { endpoint: "/api/purchasing/rate-contracts/", permission: "purchasing.view_purchaseratecontract",
  label: (row: Row) => `${String(row.number)} · ${String(row.vendor_name ?? "")} · ${String(row.index_code ?? "")}` };

/** The supplier's rate-difference note: drafted as a supplementary bill or a debit note, landed when it posts. */
export default function RateDifferenceForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/rate-differences/"
      back="/purchasing/rate-differences"
      backLabel="Rate-difference notes"
      newTitle="New rate-difference note"
      heading={(row) => `${String(row.number || "Rate difference")} · ${String(row.vendor_name ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: row.status === "landed" ? "done" : draft(row) ? "open" : "draft" })}
      permissions={{ add: "purchasing.add_ratedifferencenote" }}
      editable={() => false}
      fields={[
        { key: "contract", label: "Rate contract", kind: "pick", pick: CONTRACT, createOnly: true, show: (row: Row) => `${String(row.contract_number ?? "")} · ${String(row.vendor_name ?? "")}` },
        { key: "start", label: "Received from", kind: "date", createOnly: true },
        { key: "end", label: "Received to", kind: "date", createOnly: true },
        { key: "note_date", label: "Dated", kind: "date", createOnly: true },
        { key: "supplier_reference", label: "Supplier's note number", kind: "text", createOnly: true },
        { key: "supplier_note_date", label: "Supplier's note date", kind: "date", createOnly: true },
        { key: "total", label: "Difference", kind: "money", readOnly: true, existingOnly: true },
        { key: "bill_number", label: "Supplementary bill", kind: "text", readOnly: true, existingOnly: true },
        { key: "debit_note_number", label: "Debit note", kind: "text", readOnly: true, existingOnly: true },
      ]}
      links={[
        { label: "Open the supplementary bill", href: (row) => `/purchasing/bills/${String(row.bill)}`, when: (row) => Boolean(row.bill), same: true },
        { label: "Open the debit note", href: (row) => `/purchasing/bills/${String(row.debit_note)}`, when: (row) => Boolean(row.debit_note), same: true },
      ]}
      actions={[
        { label: "Cancel", path: "cancel", permission: "purchasing.change_ratedifferencenote", danger: true, when: draft, done: "Cancelled" },
      ]}
      panels={[{
        title: "Receipts varied", permission: "purchasing.view_ratedifferenceline", endpoint: "/api/purchasing/rate-difference-lines/",
        query: (row) => ({ note: String(row.id) }),
        columns: [
          { key: "receipt_number", label: "Receipt", width: "10rem" },
          { key: "receipt_date", label: "Received", kind: "date", width: "8rem" },
          { key: "item_name", label: "Item" },
          { key: "quantity_kg", label: "kg", kind: "quantity", width: "8rem" },
          { key: "index_was", label: "Priced at", kind: "quantity", width: "7rem" },
          { key: "index_now", label: "Revised", kind: "quantity", width: "7rem" },
          { key: "per_kg", label: "A kg", kind: "quantity", width: "6rem" },
          { key: "amount", label: "Amount", kind: "money", width: "9rem" },
        ],
      }]}
    />
  );
}
