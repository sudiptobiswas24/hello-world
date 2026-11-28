import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const coded = (row: Row) => `${String(row.code)} · ${String(row.name)}`;

export { ACCOUNT, CURRENCY, MONEY_ACCOUNT } from "../purchasing/refs";
/** A section tax is deducted under (194C, 194Q, ...). */
export const SECTION: FieldDef["ref"] = {
  endpoint: "/api/accounting/tds-sections/", permission: "accounting.view_tdssection", label: coded,
};
/** A section tax is collected at source under (206C(1) scrap). */
export const TCS_SECTION: FieldDef["ref"] = {
  endpoint: "/api/accounting/tcs-sections/", permission: "accounting.view_tcssection", label: coded,
};
/** The taxes a sale may bear, and those a purchase may: an item's defaults (O395). */
export const SALES_TAX: FieldDef["ref"] = {
  endpoint: "/api/accounting/taxes/", permission: "accounting.view_tax", query: { is_active: "true" },
  label: coded,
};
export const PURCHASE_TAX: FieldDef["ref"] = {
  endpoint: "/api/accounting/taxes/", permission: "accounting.view_tax", query: { is_active: "true" },
  label: coded,
};
/** Any party, whatever its roles. */
export const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: coded };
/** A cost centre: who incurred a cost, in the analytic view of the books. */
export const COST_CENTRE: FieldDef["pick"] = {
  endpoint: "/api/accounting/cost-centres/", permission: "accounting.view_costcentre", query: { is_active: "true" }, label: coded,
};
