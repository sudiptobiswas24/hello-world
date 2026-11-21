import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

export const VENDOR: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const CUSTOMER: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "customer" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
/** A step of a released run done outside: what a job worker is sent material for. */
export const OUTSIDE_STEP: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/work-order-operations/", permission: "manufacturing.view_workorderoperation",
  query: { work_order__status: "released", is_outside: "true" }, label: (row: Row) => String(row.label),
};

export function postedState(row: Row) {
  if (row.voided_at) return { label: "Voided", tone: "draft" };
  return row.posted ? { label: "Posted", tone: "done" } : { label: "Draft", tone: "open" };
}
export const draft = (row: Row) => !row.posted;
export const postedLabel = (row: Row) => (row.voided_at ? "Voided" : row.posted ? "Posted" : "Draft");
