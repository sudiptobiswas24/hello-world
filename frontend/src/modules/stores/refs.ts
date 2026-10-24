import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

export const WAREHOUSE: FieldDef["ref"] = {
  endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse",
  label: (row: Row) => String(row.name || row.code),
};
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item",
  label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const LOT: FieldDef["pick"] = {
  endpoint: "/api/inventory/lots/", permission: "inventory.view_lot",
  label: (row: Row) => `${String(row.code)} · ${String(row.item_label ?? "")}`,
};
export const BIN: FieldDef["ref"] = {
  endpoint: "/api/inventory/bins/", permission: "inventory.view_storagebin",
  label: (row: Row) => `${String(row.code)} · ${String(row.warehouse_name ?? "")}`,
};
export const REASON: FieldDef["ref"] = {
  endpoint: "/api/inventory/adjustment-reasons/", permission: "inventory.view_adjustmentreason",
  label: (row: Row) => String(row.name),
};
