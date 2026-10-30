import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";

const RECEIPT = { endpoint: "/api/purchasing/goods-receipts/", permission: "purchasing.view_goodsreceipt",
  query: { posted: "true", reverses__isnull: "true" },
  label: (row: Record<string, unknown>) => `${String(row.number)} · ${String(row.vendor_name ?? "")}` };
const PARTY = { endpoint: "/api/core/parties/", permission: "core.view_party",
  label: (row: Record<string, unknown>) => String(row.name) };
const BILL_LINE = { endpoint: "/api/purchasing/bill-lines/", permission: "purchasing.view_billline",
  label: (row: Record<string, unknown>) => `${String(row.bill_number ?? row.bill ?? "")} · ${String(row.description ?? "")}` };

/** A claim for goods short on the weighbridge: raised at the receipt's cost, settled through the books. */
export default function WeightClaimForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/weight-claims/"
      back="/purchasing/weight-claims"
      backLabel="Weight claims"
      newTitle="New weight claim"
      heading={(row) => String(row.number || "Weight claim")}
      state={(row) => ({ label: String(row.status), tone: row.status === "settled" ? "done" : row.status === "withdrawn" ? "draft" : "open" })}
      permissions={{ add: "purchasing.add_weightclaim" }}
      editable={() => false}
      fields={[
        { key: "receipt", label: "Receipt", kind: "pick", pick: RECEIPT, createOnly: true },
        { key: "against", label: "Against", kind: "choice", choices: [["supplier", "Supplier"], ["transporter", "Transporter"]], createOnly: true },
        { key: "transporter", label: "Transporter", kind: "pick", pick: PARTY, createOnly: true },
        { key: "quantity", label: "Quantity short", kind: "decimal", hint: "Blank claims the whole shortage", createOnly: true },
        { key: "note", label: "Note", kind: "text", createOnly: true },
        { key: "value", label: "Value at the receipt's cost", kind: "money", readOnly: true, existingOnly: true },
        { key: "debit_note_number", label: "Debit note", kind: "text", readOnly: true, existingOnly: true },
        { key: "settled_on", label: "Settled on", kind: "date", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Settle", path: "settle", permission: "purchasing.settle_weightclaim", primary: true, when: open, done: "Settled",
          fields: (record) => (record.against === "supplier"
            ? [{ key: "bill_line", label: "Bill line", kind: "pick", pick: BILL_LINE },
              { key: "supplier_note_number", label: "Supplier's note number", kind: "text" }]
            : [{ key: "on_date", label: "On", kind: "date" }]) },
        { label: "Withdraw", path: "withdraw", permission: "purchasing.change_weightclaim", danger: true,
          when: (row) => row.status !== "withdrawn" && !row.debit_note, done: "Withdrawn",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
    />
  );
}
