import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const BILL = {
  endpoint: "/api/purchasing/bills/", permission: "purchasing.view_bill",
  query: { posted: "true", debits__isnull: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.vendor_name ?? "")}`,
};

/**
 * A self-invoice made from a posted bill, within thirty days of receiving the supply (Rule 47A). Issued is a
 * fact: a wrong one is cancelled, and another issued.
 */
export default function SelfInvoiceForm() {
  return (
    <RecordScreen
      endpoint="/api/gst/self-invoices/"
      back="/accounts/self-invoices"
      backLabel="Self-invoices"
      newTitle="Issue a self-invoice"
      heading={(row) => String(row.number ?? "")}
      state={(row) => (row.cancelled_on ? { label: "Cancelled", tone: "void" } : { label: "Issued", tone: "done" })}
      permissions={{ add: "gst.add_selfinvoice" }}
      fields={[
        { key: "bill", label: "From bill", kind: "pick", pick: BILL, show: (row) => String(row.bill_number ?? "") },
        { key: "date", label: "Issued on", kind: "date", hint: "Within thirty days of receiving the supply" },
        { key: "received_on", label: "Received on", kind: "date", existingOnly: true, readOnly: true },
        { key: "supplier_name", label: "Supplier", existingOnly: true, readOnly: true },
        { key: "taxable", label: "Taxable value", kind: "money", existingOnly: true, readOnly: true },
        { key: "igst", label: "IGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cgst", label: "CGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "sgst", label: "SGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cess", label: "Cess", kind: "money", existingOnly: true, readOnly: true },
        { key: "cancelled_on", label: "Cancelled on", kind: "date", existingOnly: true, readOnly: true },
        { key: "cancellation_reason", label: "Why", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Cancel", path: "cancel", permission: "gst.change_selfinvoice", danger: true,
          when: (row) => !row.cancelled_on, done: "Cancelled",
          fields: [{ key: "reason", label: "Why" }, { key: "date", label: "Cancelled on", kind: "date" }] },
      ]}
    />
  );
}
