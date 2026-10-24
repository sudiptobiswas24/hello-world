import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

/** The short masters the plant's screens choose from, read whole and kept. */
export const MACHINE: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/machines/", permission: "manufacturing.view_machine",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const WORK_CENTRE: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const SHIFT: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/shifts/", permission: "manufacturing.view_shift",
  label: (row: Row) => String(row.name || row.code),
};
export const REASON: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/downtime-reasons/", permission: "manufacturing.view_downtimereason",
  label: (row: Row) => `${String(row.name)}${row.is_planned ? " (planned)" : ""}`,
};
export const METER: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/energy-meters/", permission: "manufacturing.view_energymeter",
  label: (row: Row) => `${String(row.code)} · ${String(row.serves ?? "")}`,
};
export const WAREHOUSE: FieldDef["ref"] = {
  endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse",
  label: (row: Row) => String(row.name || row.code),
};
export const EMPLOYEE: FieldDef["pick"] = {
  endpoint: "/api/hr/employees/", permission: "hr.view_employee",
  label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}`,
};
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item",
  label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};

/** Either a machine or a whole bank: a schedule, a job, a stoppage, a meter. */
export const SERVES: FieldDef[] = [
  { key: "work_centre", label: "Work centre", kind: "ref", ref: WORK_CENTRE, hint: "The whole bank, or the one the machine is in" },
  { key: "machine", label: "Machine", kind: "ref", ref: MACHINE, hint: "Empty for the whole work centre" },
];
