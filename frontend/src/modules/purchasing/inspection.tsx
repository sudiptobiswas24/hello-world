import type { PanelDef } from "../../views/RecordScreen";
import { WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
type Inspection = { waiting: Row[]; decided: Row[] };

const lineChoices = (receipt: Row): [string, string][] =>
  ((receipt.lines as Row[]) ?? []).map((line) => [String(line.id), String(line.description)]);
/** The batches a receipt brought in, each once: [id, code]. */
const lotsOf = (receipt: Row): [string, string][] => {
  const seen = new Map<string, string>();
  for (const line of (receipt.lines as Row[]) ?? []) if (line.lot) seen.set(String(line.lot), String(line.lot_code));
  return [...seen.entries()];
};
const inspection = (receipt: Row) => `/api/purchasing/goods-receipts/${receipt.id}/inspection/`;

/**
 * Goods held for inspection on a receipt: passed to a shelf they can ship
 * from, or failed and sent back. Each decision is kept, because "these
 * failed" is the vendor's record even after the goods are gone.
 */
export const INSPECTION_PANELS: PanelDef[] = [{
  title: "Held for inspection", permission: "purchasing.view_goodsreceipt", endpoint: "", query: () => ({}),
  read: { path: inspection, rows: (data) => (data as Inspection).waiting.map((row) => ({ ...row, id: Number(row.line) })) },
  columns: [
    { key: "item", label: "Item" },
    { key: "warehouse", label: "Held at", width: "10rem" },
    { key: "quantity", label: "Waiting", kind: "quantity", width: "9rem" },
  ],
  adder: { label: "Pass", permission: "purchasing.change_receiptinspection",
    url: (receipt) => `/api/purchasing/goods-receipts/${receipt.id}/accept/`,
    fields: (receipt) => [
      { key: "line", label: "Line", kind: "choice", choices: lineChoices(receipt) },
      { key: "quantity", label: "Quantity", kind: "decimal" },
      { key: "warehouse", label: "Clear to", kind: "ref", ref: WAREHOUSE },
    ],
    body: (values) => ({ warehouse: values.warehouse, quantities: { [String(values.line)]: values.quantity } }) },
}, {
  title: "Inspection decisions", permission: "purchasing.view_receiptinspection", endpoint: "", query: () => ({}),
  read: { path: inspection, rows: (data) => (data as Inspection).decided },
  columns: [
    { key: "on", label: "On", kind: "date", width: "9rem" },
    { key: "item", label: "Item", width: "10rem" },
    { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
    { key: "accepted", label: "", width: "7rem", render: (row) => (row.accepted ? "Passed" : "Failed") },
    { key: "note", label: "Why" },
  ],
  adder: { label: "Fail and send back", permission: "purchasing.change_receiptinspection",
    url: (receipt) => `/api/purchasing/goods-receipts/${receipt.id}/reject/`,
    fields: (receipt) => [
      { key: "line", label: "Line", kind: "choice", choices: lineChoices(receipt) },
      { key: "quantity", label: "Quantity", kind: "decimal" },
      { key: "note", label: "Why", kind: "text", hint: "It is the vendor's record" },
    ],
    body: (values) => ({ note: values.note, quantities: { [String(values.line)]: values.quantity } }) },
}, {
  // The supplier's certificate of analysis for each batch the receipt brought in (L28): quality records it, purchasing reads it.
  title: "Supplier's certificates", permission: "quality.view_suppliercertificate",
  endpoint: "/api/quality/supplier-certificates/",
  query: (receipt) => ({ lots: lotsOf(receipt).map(([id]) => id).join(",") || "0" }),
  href: (row) => `/quality/supplier-certificates/${row.id}`,
  columns: [
    // A draft has no number yet, and the first column is the link: it reads "Draft".
    { key: "number", label: "Number", width: "10rem", render: (row) => String(row.number || "Draft") },
    { key: "lot_code", label: "Batch", width: "10rem" },
    { key: "supplier_number", label: "Their number" },
    { key: "result", label: "", width: "8rem",
      render: (row) => (row.voided_at ? "Voided" : !row.posted ? "Draft" : row.result === "within" ? "Within" : "Outside") },
  ],
  adder: { label: "Record a certificate", permission: "quality.add_suppliercertificate",
    url: () => "/api/quality/supplier-certificates/",
    fields: (receipt) => [
      { key: "lot", label: "Batch", kind: "choice", choices: lotsOf(receipt) },
      { key: "supplier_number", label: "Their certificate number", kind: "text" },
      { key: "issued_on", label: "Dated", kind: "date" },
    ],
    body: (values) => ({ lot: values.lot, supplier_number: values.supplier_number, issued_on: values.issued_on }) },
}];
