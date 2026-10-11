import type { FieldDef } from "../../views/RecordScreen";
import { freightTerms, INCOTERMS } from "../parties/tradeTerms";

type Row = Record<string, unknown>;

const none: [string, string] = ["", "—"];

export const INDUSTRIES: [string, string][] = [
  none, ["cement", "Cement"], ["fertiliser", "Fertiliser"], ["food_grain", "Food grain"], ["sugar", "Sugar"],
  ["salt", "Salt"], ["feed", "Animal feed"], ["chemicals", "Chemicals"], ["other", "Other"],
];
export const FREIGHT_TERMS = freightTerms("sell");
export { INCOTERMS };

const PRICE_LIST: FieldDef["ref"] = {
  endpoint: "/api/sales/price-lists/", permission: "sales.view_pricelist", label: (row: Row) => String(row.name),
};
const VENDOR: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", query: { role_assignments__role: "vendor" },
  label: (row) => String(row.name), detail: (row) => String(row.code),
};

/**
 * Every term a customer trades on, grouped as the people who keep them
 * think of them. The rep whose customer it is is chosen beside these,
 * from the reps, since a rep is a party and the list is of reps.
 */
export const TERM_SECTIONS: { title: string; fields: FieldDef[] }[] = [
  { title: "Credit", fields: [
    { key: "credit_limit", label: "Credit limit", kind: "money", hint: "Blank: no limit" },
    { key: "price_list", label: "Price list", kind: "ref", ref: PRICE_LIST, hint: "Instead of the usual one" },
    { key: "industry", label: "Trade", kind: "choice", choices: INDUSTRIES, hint: "What they fill the sacks with" },
    { key: "credit_hold", label: "On credit hold", kind: "bool",
      hint: "No order confirmed and nothing dispatched until it is lifted" },
    { key: "credit_hold_reason", label: "Why on hold", hint: "Who meets the refusal is told this" },
  ] },
  { title: "Delivery", fields: [
    { key: "freight_terms", label: "Freight", kind: "choice", choices: FREIGHT_TERMS },
    { key: "transporter", label: "Usual transporter", kind: "pick", pick: VENDOR, hint: "A new delivery takes them" },
    { key: "incoterm", label: "Incoterm", kind: "choice", choices: INCOTERMS, hint: "For an export customer" },
    { key: "port_of_discharge", label: "Port of discharge", hint: "For an export customer: Jebel Ali" },
  ] },
  { title: "Packing", fields: [
    { key: "sacks_per_bale", label: "Sacks a bale", kind: "integer" },
    { key: "marking", label: "Marking and printing", kind: "textarea", hint: "Printed on the run's card: batch, date, brand" },
  ] },
  { title: "Quality", fields: [
    { key: "virgin_only", label: "Virgin material only", kind: "bool", hint: "No regrind anywhere in what they are sold" },
    { key: "max_filler_percent", label: "Most filler %", kind: "decimal", places: 2 },
    { key: "min_uv_percent", label: "Least UV stabiliser %", kind: "decimal", places: 2 },
    { key: "third_party_inspection", label: "Their inspector passes each batch", kind: "bool" },
    { key: "release_covers_returns", label: "Returns go back under the release", kind: "bool" },
  ] },
  { title: "Quantity", fields: [
    { key: "over_delivery_percent", label: "May deliver over by %", kind: "decimal", places: 2 },
    { key: "under_delivery_percent", label: "Short by % still meets the order", kind: "decimal", places: 2 },
  ] },
];

export const TERM_FIELDS: FieldDef[] = TERM_SECTIONS.flatMap((section) => section.fields);
