import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

/** How an address reads in a list: its label, where it has one, then the place. */
export const addressLabel = (row: Row) =>
  [row.label, row.line1, row.city, row.state, row.postal_code].filter(Boolean).map(String).join(", ");

/**
 * The place in India an export's goods leave the plant for: the port, ICD
 * or CFS, kept under Accounts > Ports and depots. The server refuses one
 * with no Indian PIN or GST state, in words, as it is named.
 */
export const PORT: NonNullable<FieldDef["pick"]> = {
  endpoint: "/api/core/addresses/", permission: "core.view_address", query: { is_active: "true" }, label: addressLabel,
};

export const PORT_HINT = "For an export of goods: the port, ICD or CFS in India they leave the plant for";
