import { dateTime } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { CLAIM_REASONS } from "../sales/claimReasons";
import { correctiveActions } from "./capa";
import { CUSTOMER, EMPLOYEE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";
const investigation = (row: Row) => `/api/manufacturing/complaints/${row.id}/investigation/`;
type Found = { made_from: Record<string, Row[]>; tape_settings: Row[] };

const CATEGORIES: [string, string][] = [
  ["weight", "Weight or size"], ["strength", "Strength or bursting"], ["seam", "Stitching or seam"],
  ["print", "Print"], ["lamination", "Lamination or coating"], ["contamination", "Contamination"],
  ["count", "Count short"], ["other", "Other"],
];

/**
 * A complaint: the batches it names, traced back to what they were made
 * from and forward to who else holds them, and the actions taken. It is
 * closed with its root cause, or rejected with the reason.
 */
export default function ComplaintForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/complaints/"
      back="/quality/complaints"
      backLabel="Complaints"
      newTitle="New complaint"
      heading={(row) => `${String(row.number)} · ${String(row.customer_name ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: row.status === "open" ? "open" : row.status === "closed" ? "done" : "draft" })}
      permissions={{ add: "manufacturing.add_complaint", change: "manufacturing.change_complaint" }}
      editable={open}
      fields={[
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER, createOnly: true, show: (row) => String(row.customer_name ?? "") },
        { key: "received_on", label: "Received", kind: "date" },
        { key: "category", label: "About", kind: "choice", choices: CATEGORIES },
        { key: "quantity_affected", label: "How many", kind: "decimal" },
        { key: "description", label: "What they said", kind: "textarea" },
        { key: "root_cause", label: "Root cause", readOnly: true },
        { key: "rejection_reason", label: "Rejected because", readOnly: true },
      ]}
      actions={[
        { label: "Settle by credit", path: "settle", permission: "sales.post_invoice", done: "Credit note posted",
          when: (row) => row.status !== "rejected",
          fields: (row) => [
            { key: "invoice", label: "On invoice", kind: "pick", pick: { endpoint: "/api/sales/invoices/", permission: "sales.view_invoice",
              query: { customer: String(row.customer), posted: "true", credits__isnull: "true" },
              label: (invoice: Record<string, unknown>) => `${String(invoice.number)} · ${String(invoice.invoice_date)}` } } as FieldDef,
            { key: "reason", label: "For", kind: "choice", choices: CLAIM_REASONS },
            { key: "net", label: "Given back, before tax", kind: "money" },
          ] },
        { label: "Name a batch", path: "lots", permission: "manufacturing.change_complaint", when: open, done: "Batch added",
          fields: [{ key: "lot", label: "Batch code", kind: "text" }, { key: "quantity", label: "How many of it", kind: "decimal" }] },
        { label: "Close", path: "close", permission: "manufacturing.change_complaint", when: open, primary: true, done: "Closed",
          fields: [{ key: "root_cause", label: "Root cause", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Reject", path: "reject", permission: "manufacturing.change_complaint", when: open, danger: true, done: "Rejected",
          fields: [{ key: "reason", label: "Why", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Reopen", path: "reopen", permission: "manufacturing.change_complaint", when: (row) => !open(row), done: "Reopened",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[
        { title: "Batches", permission: "manufacturing.view_complaint", endpoint: "", query: () => ({}),
          rows: (record) => ((record.lots as Row[]) ?? []).map((row, index) => ({ ...row, id: index })),
          columns: [{ key: "lot", label: "Batch" }, { key: "item", label: "Item" }, { key: "quantity", label: "How many", kind: "quantity" }] },
        { title: "Made from", permission: "manufacturing.view_complaint", endpoint: "", query: () => ({}),
          read: { path: investigation, rows: (data) => Object.entries((data as Found).made_from).flatMap(([batch, rows]) =>
            rows.map((row, index) => ({ ...row, batch, id: `${batch}-${index}` }) as unknown as Row)) },
          columns: [
            { key: "batch", label: "Batch", width: "9rem" },
            { key: "made_by", label: "Run", width: "8rem" },
            { key: "from_item", label: "From" },
            { key: "from_lot", label: "Its batch", width: "9rem" },
            { key: "quantity", label: "How much", kind: "quantity", width: "9rem" },
          ] },
        { title: "Tape line settings on those runs", permission: "manufacturing.view_taperunsetting", endpoint: "", query: () => ({}),
          read: { path: investigation, rows: (data) => (data as Found).tape_settings.map((row, index) => ({ ...row, id: index })) },
          columns: [
            { key: "run", label: "Run", width: "8rem" },
            { key: "recorded_at", label: "At", width: "11rem", render: (row: Row) => dateTime(row.recorded_at as string) },
            { key: "machine", label: "Machine", width: "8rem" },
            { key: "draw_ratio", label: "Draw ratio", kind: "quantity", width: "8rem" },
            { key: "quench_temperature_c", label: "Quench °C", kind: "quantity", width: "8rem" },
            { key: "oven_temperature_c", label: "Oven °C", kind: "quantity", width: "8rem" },
            { key: "note", label: "Note" },
          ] },
        correctiveActions("complaint"),
      ]}
    />
  );
}
