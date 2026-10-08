import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const coded = (row: Row) => `${String(row.code)} · ${String(row.name)}`;

export const VENDOR: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" }, label: coded,
};
/** Whoever asks for something: an employee, as the party they are. */
export const REQUESTER: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "employee" }, label: coded,
};
export const ITEM: FieldDef["pick"] = {
  endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}`,
};
export const UOM: FieldDef["ref"] = {
  endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure", label: (row: Row) => String(row.code),
};
export const WAREHOUSE: FieldDef["ref"] = {
  endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse", label: (row: Row) => String(row.name || row.code),
};
export const ACCOUNT: FieldDef["pick"] = {
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: coded,
};
/** What money is paid from or into: an open bank, cash or card account. The server refuses the rest. */
export const MONEY_ACCOUNT: FieldDef["pick"] = {
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: coded,
  query: { holds_money: "true", is_active: "true" },
};
/** Anything but a bank, cash or card account: where a document or a setting keeps what is owed, held or taxed. */
export const KEPT_ACCOUNT: FieldDef["pick"] = {
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: coded, query: { holds_money: "false" },
};
export const CURRENCY: FieldDef["ref"] = {
  endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code),
};

export const STATUS_TONE: Record<string, string> = {
  draft: "draft", submitted: "open", sent: "open", approved: "open", confirmed: "open",
  ordered: "done", awarded: "done", closed: "done", rejected: "cancelled", cancelled: "cancelled",
};
export const status = (row: Row) => {
  const value = String(row.status ?? "draft");
  return { label: value.charAt(0).toUpperCase() + value.slice(1), tone: STATUS_TONE[value] ?? "draft" };
};
