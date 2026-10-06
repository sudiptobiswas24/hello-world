import type { PanelDef } from "../../views/RecordScreen";
import { WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
type Inspection = { waiting: Row[]; decided: Row[] };

const lineChoices = (receipt: Row): [string, string][] =>
  ((receipt.lines as Row[]) ?? []).map((line) => [String(line.id), String(line.description)]);
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
  adder: { label: "Pass", permission: "purchasing.add_receiptinspection",
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
  adder: { label: "Fail and send back", permission: "purchasing.add_receiptinspection",
    url: (receipt) => `/api/purchasing/goods-receipts/${receipt.id}/reject/`,
    fields: (receipt) => [
      { key: "line", label: "Line", kind: "choice", choices: lineChoices(receipt) },
      { key: "quantity", label: "Quantity", kind: "decimal" },
      { key: "note", label: "Why", kind: "text", hint: "It is the vendor's record" },
    ],
    body: (values) => ({ note: values.note, quantities: { [String(values.line)]: values.quantity } }) },
}];
