import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

/** The masters how-it's-made screens choose from. */
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item",
  label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const UOM: FieldDef["ref"] = {
  endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure",
  label: (row: Row) => String(row.code),
};
export const ROUTING: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`, query: { is_active: "true" },
};
export const WORK_CENTRE: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const SHIFT: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/shifts/", permission: "manufacturing.view_shift",
  label: (row: Row) => String(row.name || row.code),
};
export const EMPLOYEE: FieldDef["pick"] = {
  endpoint: "/api/hr/employees/", permission: "hr.view_employee",
  label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}`,
};

export const SPEED_BASES: [string, string][] = [
  ["stated", "Stated rate"], ["tape_line", "Tape line"], ["circular_loom", "Circular loom"],
  ["web", "Web speed (metres a minute)"],
];
export const TOOL_KINDS: [string, string][] = [
  ["cylinder", "Printing cylinder"], ["die", "Cutting die"], ["reed", "Loom reed"],
  ["screen", "Extruder screen"], ["other", "Other"],
];
export const TOOL_STATUSES: [string, string][] = [
  ["available", "Available"], ["worn", "Worn out"], ["service", "Away for service"],
  ["ordered", "On order"], ["retired", "Retired"],
];
export const BYPRODUCT_VALUATIONS: [string, string][] = [
  ["standard", "At the item's standard cost"], ["share", "At a share of what the run cost"], ["none", "At nothing"],
];
export const VENDOR: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
