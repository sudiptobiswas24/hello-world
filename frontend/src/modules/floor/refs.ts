import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

/** A run that is on the floor: released, not yet closed. */
export const RUN: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/work-orders/", permission: "manufacturing.view_workorder", query: { status: "released" },
  label: (row: Row) => `${String(row.number)} · ${String(row.item_sku)} ${String(row.item_name)}`,
};
/** A step of a released run: which run it belongs to comes with it. */
export const STEP: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/work-order-operations/", permission: "manufacturing.view_workorderoperation",
  query: { work_order__status: "released" }, label: (row: Row) => String(row.label),
};
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item",
  label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const LOT: FieldDef["pick"] = {
  endpoint: "/api/inventory/lots/", permission: "inventory.view_lot",
  label: (row: Row) => `${String(row.code)} · ${String(row.item_label ?? "")}`,
};
export const UOM: FieldDef["ref"] = {
  endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure", label: (row: Row) => String(row.code),
};
export const WAREHOUSE: FieldDef["ref"] = {
  endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse", label: (row: Row) => String(row.name || row.code),
};
export const MACHINE: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/machines/", permission: "manufacturing.view_machine",
  label: (row: Row) => `${String(row.code)} · ${String(row.name || row.work_centre_name || "")}`,
};
export const SHIFT: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/shifts/", permission: "manufacturing.view_shift", label: (row: Row) => String(row.name || row.code),
};
export const SCRAP_REASON: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/scrap-reasons/", permission: "manufacturing.view_scrapreason",
  label: (row: Row) => String(row.name), query: { is_active: "true" },
};
export const DIRECTIONS: [string, string][] = [["issue", "Issued to the run"], ["return", "Returned to the store"]];

/** Posted, voided or a draft: the same three states for every floor document. */
export function postedState(row: Row) {
  if (row.voided_at) return { label: "Voided", tone: "draft" };
  return row.posted ? { label: "Posted", tone: "done" } : { label: "Draft", tone: "open" };
}
export const draft = (row: Row) => !row.posted;
export const VOID_FIELDS: FieldDef[] = [
  { key: "memo", label: "Why", kind: "text" },
  { key: "on_date", label: "Reversed on", kind: "date", hint: "Empty: today" },
];
