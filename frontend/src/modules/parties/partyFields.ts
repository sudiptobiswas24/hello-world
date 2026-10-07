import type { FieldDef } from "../../views/RecordScreen";
import { SECTION } from "../accounts/refs";

type Row = Record<string, unknown>;

const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };
const TERMS: FieldDef["ref"] = { endpoint: "/api/core/payment-terms/", permission: "core.view_paymentterms", label: (row: Row) => String(row.name) };

/** Who a customer or a vendor is: every box the party itself stores. */
export const PARTY_FIELDS: FieldDef[] = [
  { key: "code", label: "Code", hint: "Short and unique: what people type to find them" },
  { key: "name", label: "Name" },
  { key: "legal_name", label: "Legal name", hint: "As it must appear on invoices, if different" },
  { key: "parent", label: "Part of", kind: "pick", hint: "The group they belong to, if any",
    pick: { endpoint: "/api/core/parties/", label: (row) => String(row.name), detail: (row) => String(row.code) } },
  { key: "cin", label: "CIN", hint: "Corporate identity number, for a company" },
  { key: "iec", label: "IEC", hint: "Import-export code, for one who trades across the border" },
  { key: "tax_id", label: "Other tax number", hint: "A foreign tax number. The GST registration and PAN are kept under GST" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email", hint: "Where invoices and statements are sent" },
  { key: "website", label: "Website" },
  { key: "default_currency", label: "Currency", kind: "ref", ref: CURRENCY, hint: "What a new document for them is in" },
  { key: "payment_terms", label: "Payment terms", kind: "ref", ref: TERMS, hint: "What a new document for them takes" },
  { key: "notes", label: "Notes", kind: "textarea" },
];

export const ADDRESS_TYPES: [string, string][] = [["billing", "Billing"], ["shipping", "Shipping"], ["office", "Office"], ["other", "Other"]];

const COUNTRY: FieldDef["ref"] = {
  endpoint: "/api/core/countries/", permission: "core.view_country", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};

export const ADDRESS_FIELDS: FieldDef[] = [
  { key: "line1", label: "Line 1" },
  { key: "line2", label: "Line 2" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "postal_code", label: "PIN code" },
  { key: "country", label: "Country", kind: "ref", ref: COUNTRY },
];

export const CONTACT_FIELDS: FieldDef[] = [
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "job_title", label: "Job title", hint: "Purchase manager, accounts" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "mobile", label: "Mobile" },
];

export const GST_FIELDS: FieldDef[] = [
  { key: "gst_registration", label: "GST standing", kind: "choice", choices: [
    ["", "—"], ["regular", "Registered, regular"], ["composition", "Registered, composition"],
    ["sez", "Special economic zone"], ["unregistered", "Unregistered"], ["overseas", "Overseas"],
  ] },
  { key: "gstin", label: "GSTIN", hint: "Checked character by character; its state and PAN are read off it" },
  { key: "gst_state", label: "GST state code", hint: "Only for an unregistered party: 27 for Maharashtra" },
  { key: "pan", label: "PAN", hint: "Only without a GSTIN" },
];

/** A vendor's MSME and TDS standing, kept on its tax profile beside the GSTIN. */
export const MSME_TDS_FIELDS: FieldDef[] = [
  { key: "msme_category", label: "MSME", kind: "choice", choices: [["", "—"], ["micro", "Micro"], ["small", "Small"], ["medium", "Medium"]],
    hint: "As their Udyam registration says; micro and small are paid within 45 days" },
  { key: "udyam_number", label: "Udyam number", hint: "UDYAM-XX-00-0000000" },
  { key: "tds_section", label: "TDS section", kind: "ref", ref: SECTION, hint: "What their bills are deducted under" },
];

/** The account a vendor is paid into. */
export const BANK_FIELDS: FieldDef[] = [
  { key: "account_name", label: "In the name of" },
  { key: "bank_name", label: "Bank" },
  { key: "account_number", label: "Account number" },
  { key: "ifsc", label: "IFSC", hint: "The branch's code for NEFT and RTGS: HDFC0001234" },
];
