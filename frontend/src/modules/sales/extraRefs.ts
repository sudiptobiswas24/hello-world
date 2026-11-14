import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

/** A line of an order: the order, the customer and what the line is. */
export const ORDER_LINE = (query: Record<string, string> = {}): FieldDef["pick"] => ({
  endpoint: "/api/sales/sales-order-lines/", permission: "sales.view_salesorderline", query,
  label: (row: Row) => `${String(row.order_number)} · ${String(row.customer_name)} · ${String(row.label)}`,
});
export const ORDER: FieldDef["pick"] = {
  endpoint: "/api/sales/sales-orders/", permission: "sales.view_salesorder",
  label: (row: Row) => `${String(row.number || "Draft")} · ${String(row.customer_name)}`,
};
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const CUSTOMER: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "customer" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const EMPLOYEE_PARTY: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "employee" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const ACCOUNT: FieldDef["pick"] = {
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const INDEX: FieldDef["ref"] = {
  endpoint: "/api/sales/price-indices/", permission: "sales.view_priceindex", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const PLAN: FieldDef["ref"] = {
  endpoint: "/api/sales/commission-plans/", permission: "sales.view_commissionplan", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const REP: FieldDef["ref"] = {
  endpoint: "/api/sales/sales-reps/", permission: "sales.view_salesrep", label: (row: Row) => String(row.name),
};
export const TEAM: FieldDef["ref"] = {
  endpoint: "/api/sales/sales-teams/", permission: "sales.view_salesteam", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
