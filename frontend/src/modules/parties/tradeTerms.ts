import type { FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const none: [string, string] = ["", "—"];

export const INCOTERMS: [string, string][] = [
  none, ["EXW", "EXW: ex works"], ["FCA", "FCA: free carrier"], ["FOB", "FOB: free on board"],
  ["CFR", "CFR: cost and freight"], ["CIF", "CIF: cost, insurance and freight"], ["DAP", "DAP: delivered at place"],
  ["DDP", "DDP: delivered duty paid"],
];

/**
 * Freight terms in the words of whoever keeps them: the server holds one
 * set for both sides, and "they collect" on a sale is "we collect" on a
 * purchase.
 */
export function freightTerms(side: "sell" | "buy"): [string, string][] {
  return side === "sell"
    ? [none, ["ex_works", "Ex works: they collect"], ["for_destination", "FOR destination: we deliver, the freight is ours"],
      ["to_pay", "To pay: we send, they pay the transporter"]]
    : [none, ["ex_works", "Ex works: we collect"], ["for_destination", "FOR destination: they deliver, the freight is theirs"],
      ["to_pay", "To pay: they send, we pay the transporter"]];
}

const PAYMENT_TERMS: FieldDef["ref"] = {
  endpoint: "/api/core/payment-terms/", permission: "core.view_paymentterms", label: (row: Row) => String(row.name),
};

/**
 * What an order keeps of how it is paid and how its goods travel: taken
 * from the party's terms when the order is made, and the order's own from
 * then on, so a later change to the party does not rewrite it.
 */
export function orderTermFields(side: "sell" | "buy"): FieldDef[] {
  return [
    { key: "payment_terms", label: "Payment terms", kind: "ref", ref: PAYMENT_TERMS },
    { key: "freight_terms", label: "Freight", kind: "choice", choices: freightTerms(side),
      hint: side === "sell" ? "Fixed once anything has shipped" : "Fixed once anything has come in" },
    { key: "incoterm", label: "Incoterm", kind: "choice", choices: INCOTERMS },
    side === "sell"
      ? { key: "port_of_discharge", label: "Port of discharge", hint: "For an export: Jebel Ali" }
      : { key: "port_of_loading", label: "Port of loading", hint: "For an import: Shanghai" },
  ];
}
