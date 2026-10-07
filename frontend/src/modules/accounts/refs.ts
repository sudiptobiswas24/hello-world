import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const coded = (row: Row) => `${String(row.code)} · ${String(row.name)}`;

export { ACCOUNT } from "../purchasing/refs";
/** A section tax is deducted under (194C, 194Q, ...). */
export const SECTION: FieldDef["ref"] = {
  endpoint: "/api/accounting/tds-sections/", permission: "accounting.view_tdssection", label: coded,
};
/** Any party, whatever its roles. */
export const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: coded };
