import type { TermSection } from "../parties/NewParty";
import { freightTerms, INCOTERMS } from "../parties/tradeTerms";

export const STANDINGS: [string, string][] = [
  ["approved", "Approved"], ["trial", "On trial: each order to them is approved first"], ["blocked", "Blocked: no new order"],
];

/**
 * What we buy from a vendor on. Whether we buy from them at all, and
 * whether their money is held, are controls rather than terms: the buyer
 * who keeps the freight and the lead time does not unblock a vendor or
 * release a payment, and each control names the permission that may.
 */
export const VENDOR_TERM_SECTIONS: TermSection[] = [
  { title: "Supply", fields: [
    { key: "lead_time_days", label: "Usual lead time, days", kind: "integer", hint: "Planning reads it where no agreed price says" },
    { key: "freight_terms", label: "Freight", kind: "choice", choices: freightTerms("buy") },
    { key: "incoterm", label: "Incoterm", kind: "choice", choices: INCOTERMS, hint: "For an import" },
    { key: "port_of_loading", label: "Port of loading", hint: "For an import: Shanghai" },
    { key: "our_account_number", label: "Our account with them", hint: "Printed on our orders to them" },
  ] },
  { title: "Standing", control: "purchasing.set_vendor_standing", fields: [
    { key: "standing", label: "Standing", kind: "choice", choices: STANDINGS },
    { key: "standing_reason", label: "Why", hint: "Whoever meets the refusal or the approval is told this" },
  ] },
  { title: "Payments", control: "purchasing.hold_vendor_payments", fields: [
    { key: "payment_hold", label: "Payments held", kind: "bool", hint: "Nothing is paid to them until it is lifted; what they refund still comes in" },
    { key: "payment_hold_reason", label: "Why held", hint: "A quality claim, a dispute: whoever pays them is told this" },
  ] },
];

export const VENDOR_TERM_FIELDS = VENDOR_TERM_SECTIONS.flatMap((section) => section.fields);
