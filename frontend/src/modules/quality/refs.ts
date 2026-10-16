import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const LOT: FieldDef["pick"] = {
  endpoint: "/api/inventory/lots/", permission: "inventory.view_lot", label: (row: Row) => `${String(row.code)} · ${String(row.item_label ?? "")}`,
};
export const EMPLOYEE: FieldDef["pick"] = {
  endpoint: "/api/hr/employees/", permission: "hr.view_employee", label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}`,
};
/** A person as a party (who inspected), not as an employee record. */
export const PERSON: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "employee" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const PLAN: FieldDef["ref"] = {
  endpoint: "/api/quality/plans/", permission: "quality.view_inspectionplan",
  label: (row: Row) => String(row.name), query: { is_active: "true" },
};
export const CHARACTERISTIC: FieldDef["ref"] = {
  endpoint: "/api/quality/characteristics/", permission: "quality.view_characteristic",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const INSTRUMENT: FieldDef["ref"] = {
  endpoint: "/api/quality/instruments/", permission: "quality.view_instrument",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const CUSTOMER: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "customer" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};

export const DISPOSITIONS: [string, string][] = [
  ["", "Not decided"], ["accept", "Accepted"], ["concession", "Accepted by concession"],
  ["rework", "Held for rework"], ["reject", "Rejected"],
];
