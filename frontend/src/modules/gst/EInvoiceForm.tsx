import { dateTime } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const INVOICE: FieldDef["pick"] = {
  endpoint: "/api/sales/invoices/", permission: "sales.view_invoice", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name)}`,
};
const json = (value: unknown) => <pre className="payload">{JSON.stringify(value, null, 2)}</pre>;

/**
 * An invoice's registration with the GST portal: the payload built here
 * to be uploaded, and the portal's answer kept against it. Nothing is
 * sent from this screen.
 */
export default function EInvoiceForm() {
  return (
    <RecordScreen
      endpoint="/api/gst/e-invoices/"
      back="/accounts/e-invoices"
      backLabel="E-invoices"
      newTitle="New e-invoice"
      heading={(row) => String(row.invoice_number ?? "")}
      state={(row) => (row.irn ? { label: "Registered", tone: "done" } : { label: "To upload", tone: "open" })}
      permissions={{ add: "gst.add_einvoice", delete: "gst.delete_einvoice" }}
      editable={(row) => !row.irn}
      fields={[
        { key: "invoice", label: "Invoice", kind: "pick", pick: INVOICE, createOnly: true, show: (row) => String(row.invoice_number ?? "") },
        { key: "irn", label: "IRN", readOnly: true, existingOnly: true, wide: true },
        { key: "ack_number", label: "Acknowledgement number", readOnly: true, existingOnly: true },
        { key: "ack_date", label: "Acknowledged", readOnly: true, existingOnly: true, show: (row) => (row.ack_date ? dateTime(String(row.ack_date)) : "—") },
        { key: "warnings", label: "Warnings", readOnly: true, existingOnly: true, wide: true,
          show: (row) => ((row.warnings as string[] | undefined) ?? []).join(" ") || "None" },
        { key: "payload", label: "What to upload", readOnly: true, existingOnly: true, wide: true, show: (row) => json(row.payload) },
      ]}
      actions={[
        { label: "Record the portal's answer", path: "record", permission: "gst.change_einvoice", primary: true,
          when: (row) => !row.irn, done: "Registration recorded", fields: [
            { key: "irn", label: "IRN", wide: true },
            { key: "ack_number", label: "Acknowledgement number" },
            { key: "ack_date", label: "Acknowledged at", hint: "YYYY-MM-DDTHH:MM, as the portal gave it" },
            { key: "signed_qr", label: "Signed QR", kind: "textarea", wide: true },
          ] },
      ]}
    />
  );
}
