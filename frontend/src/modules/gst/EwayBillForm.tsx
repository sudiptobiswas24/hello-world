import { dateTime } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const INVOICE: FieldDef["pick"] = {
  endpoint: "/api/sales/invoices/", permission: "sales.view_invoice", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name)}`,
};
const DELIVERY: FieldDef["pick"] = {
  endpoint: "/api/sales/deliveries/", permission: "sales.view_delivery", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name)}`,
};
const CHALLAN: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/job-work-challans/", permission: "manufacturing.view_jobworkchallan", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.job_worker_name)}`,
};
const json = (value: unknown) => <pre className="payload">{JSON.stringify(value, null, 2)}</pre>;

/**
 * The movement of goods a GST e-way bill covers: built from an invoice, a
 * delivery or a job-work challan with the vehicle and distance, generated
 * on the portal, the number kept here, and cancelled within a day.
 */
export default function EwayBillForm() {
  return (
    <RecordScreen
      endpoint="/api/gst/eway-bills/"
      back="/accounts/eway-bills"
      backLabel="E-way bills"
      newTitle="New e-way bill"
      heading={(row) => String(row.number || row.vehicle_number || "E-way bill")}
      state={(row) => (row.cancelled_at ? { label: "Cancelled", tone: "draft" } : row.number ? { label: "Generated", tone: "done" } : { label: "To generate", tone: "open" })}
      permissions={{ add: "gst.add_ewaybill", delete: "gst.delete_ewaybill" }}
      editable={(row) => !row.number}
      fields={[
        { key: "invoice", label: "For invoice", kind: "pick", pick: INVOICE, newOnly: true, hint: "One of invoice, delivery or challan" },
        { key: "delivery", label: "Or delivery", kind: "pick", pick: DELIVERY, newOnly: true },
        { key: "challan", label: "Or job-work challan", kind: "pick", pick: CHALLAN, newOnly: true },
        { key: "mode", label: "Mode", kind: "choice", choices: [["1", "Road"], ["2", "Rail"], ["3", "Air"], ["4", "Ship"]], initial: "1", createOnly: true },
        { key: "distance_km", label: "Distance (km)", kind: "integer", createOnly: true },
        { key: "transporter_id", label: "Transporter GSTIN or id", createOnly: true },
        { key: "transporter_name", label: "Transporter", createOnly: true },
        { key: "vehicle_number", label: "Vehicle", createOnly: true },
        { key: "vehicle_type", label: "Vehicle type", kind: "choice", choices: [["R", "Regular"], ["O", "Over-dimensional cargo"]], initial: "R", createOnly: true },
        { key: "transport_doc_number", label: "LR or RR number", createOnly: true },
        { key: "transport_doc_date", label: "LR date", kind: "date", createOnly: true },
        { key: "required_because", label: "Why it is needed", readOnly: true, existingOnly: true, wide: true },
        { key: "number", label: "Number", readOnly: true, existingOnly: true },
        { key: "valid_until", label: "Valid until", readOnly: true, existingOnly: true, show: (row) => (row.valid_until ? dateTime(String(row.valid_until)) : "—") },
        { key: "payload", label: "What to upload", readOnly: true, existingOnly: true, wide: true, show: (row) => json(row.payload) },
      ]}
      actions={[
        { label: "Record the number", path: "record", permission: "gst.change_ewaybill", primary: true,
          when: (row) => !row.number, done: "Number recorded", fields: [
            { key: "number", label: "E-way bill number" },
            { key: "generated_at", label: "Generated at", hint: "YYYY-MM-DDTHH:MM" },
            { key: "valid_until", label: "Valid until", hint: "YYYY-MM-DDTHH:MM" },
          ] },
        { label: "Cancel it", path: "cancel", permission: "gst.change_ewaybill", danger: true,
          when: (row) => Boolean(row.number) && !row.cancelled_at, done: "Cancelled", fields: [
            { key: "reason", label: "Why", kind: "choice", choices: [["1", "Duplicate"], ["2", "Order cancelled"], ["3", "Data entry mistake"], ["4", "Others"]] },
            { key: "remarks", label: "Remarks", wide: true },
          ] },
      ]}
    />
  );
}
