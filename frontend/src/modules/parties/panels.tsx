import type { FieldDef, PanelDef, RowAction } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const COUNTRY: FieldDef["ref"] = {
  endpoint: "/api/core/countries/", permission: "core.view_country", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };
const POSITION: FieldDef["ref"] = {
  endpoint: "/api/accounting/fiscal-positions/", permission: "accounting.view_fiscalposition",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};

const ADDRESS_TYPES: [string, string][] = [["billing", "Billing"], ["shipping", "Shipping"], ["office", "Office"], ["other", "Other"]];
const REGISTRATIONS: [string, string][] = [
  ["regular", "Registered, regular"], ["composition", "Registered, composition"], ["sez", "Special economic zone"],
  ["unregistered", "Unregistered"], ["overseas", "Overseas"],
];
const named = (choices: [string, string][], value: unknown) => choices.find(([key]) => key === value)?.[1] ?? String(value ?? "");
const state = (row: Row) => (row.is_active === false ? "Archived" : row.is_primary ? "Primary" : "");

/**
 * Made the one printed and paid to, archived, or restored: never deleted,
 * since a posted invoice may name it.
 */
function kept(noun: string, endpoint: string, model: string): RowAction[] {
  const at = (row: Row) => `${endpoint}${String(row.id)}/`;
  return [
    { label: "Make primary", permission: `core.change_${model}`, method: "PATCH", body: () => ({ is_primary: true }),
      when: (row) => !row.is_primary && row.is_active !== false, url: at, done: `Primary ${noun} set` },
    { label: "Archive", permission: `core.change_${model}`, method: "PATCH", body: () => ({ is_active: false, is_primary: false }),
      when: (row) => row.is_active !== false, url: at, done: "Archived" },
    { label: "Restore", permission: `core.change_${model}`, method: "PATCH", body: () => ({ is_active: true }),
      when: (row) => row.is_active === false, url: at, done: "Restored" },
  ];
}

/** What a customer's or a vendor's page holds beside who they are. */
export const PARTY_PANELS: PanelDef[] = [
  {
    title: "Addresses", permission: "core.view_address", endpoint: "/api/core/addresses/",
    query: (party) => ({ party: String(party.id) }),
    columns: [
      { key: "address_type", label: "For", width: "7rem", render: (row) => named(ADDRESS_TYPES, row.address_type) },
      { key: "label", label: "Label", width: "10rem" },
      { key: "one_line", label: "Address" },
      { key: "is_primary", label: "", width: "6rem", render: state },
    ],
    adder: {
      label: "Add an address", permission: "core.add_address", url: () => "/api/core/addresses/",
      fields: [
        { key: "address_type", label: "For", kind: "choice", choices: ADDRESS_TYPES },
        { key: "label", label: "Label", hint: "Head office, Godown 2" },
        { key: "line1", label: "Line 1" },
        { key: "line2", label: "Line 2" },
        { key: "city", label: "City" },
        { key: "state", label: "State" },
        { key: "postal_code", label: "PIN code" },
        { key: "country", label: "Country", kind: "ref", ref: COUNTRY },
        { key: "is_primary", label: "Primary for its kind", kind: "bool" },
      ],
      body: (values, party) => ({ ...values, party: party.id }),
    },
    rowActions: kept("address", "/api/core/addresses/", "address"),
  },
  {
    title: "Contacts", permission: "core.view_contact", endpoint: "/api/core/contacts/",
    query: (party) => ({ party: String(party.id) }),
    columns: [
      { key: "full_name", label: "Name" },
      { key: "job_title", label: "Title", width: "10rem" },
      { key: "email", label: "Email" },
      { key: "mobile", label: "Mobile", width: "9rem" },
      { key: "phone", label: "Phone", width: "9rem" },
      { key: "is_primary", label: "", width: "6rem", render: state },
    ],
    adder: {
      label: "Add a contact", permission: "core.add_contact", url: () => "/api/core/contacts/",
      fields: [
        { key: "first_name", label: "First name" },
        { key: "last_name", label: "Last name" },
        { key: "job_title", label: "Title", hint: "Purchase manager, stores" },
        { key: "email", label: "Email" },
        { key: "mobile", label: "Mobile" },
        { key: "phone", label: "Phone" },
        { key: "is_primary", label: "The one to call first", kind: "bool" },
      ],
      body: (values, party) => ({ ...values, party: party.id }),
    },
    rowActions: kept("contact", "/api/core/contacts/", "contact"),
  },
  {
    title: "Bank accounts", permission: "core.view_partybankaccount", endpoint: "/api/core/bank-accounts/",
    query: (party) => ({ party: String(party.id) }),
    columns: [
      { key: "account_name", label: "In the name of" },
      { key: "bank_name", label: "Bank" },
      { key: "account_number", label: "Account", width: "11rem" },
      { key: "swift_bic", label: "SWIFT / BIC", width: "8rem" },
      { key: "currency_code", label: "Currency", width: "6rem" },
      { key: "is_primary", label: "", width: "6rem", render: state },
    ],
    adder: {
      label: "Add a bank account", permission: "core.add_partybankaccount", url: () => "/api/core/bank-accounts/",
      fields: [
        { key: "account_name", label: "In the name of" },
        { key: "bank_name", label: "Bank" },
        { key: "account_number", label: "Account number" },
        { key: "iban", label: "IBAN", hint: "For a foreign account, if it has one" },
        { key: "swift_bic", label: "SWIFT / BIC" },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
        { key: "is_primary", label: "The one paid into", kind: "bool" },
      ],
      body: (values, party) => ({ ...values, party: party.id }),
    },
    rowActions: kept("bank account", "/api/core/bank-accounts/", "partybankaccount"),
  },
  {
    // What GST reads: an e-invoice, GSTR-1 and the place of supply all
    // come from here, never from the free tax number above.
    title: "GST", permission: "accounting.view_partytaxprofile", endpoint: "/api/accounting/party-tax-profiles/",
    query: (party) => ({ party: String(party.id) }),
    href: (row) => `/settings/gst-registrations/${String(row.id)}`,
    columns: [
      { key: "gstin", label: "GSTIN", width: "12rem" },
      { key: "gst_state", label: "State", width: "5rem" },
      { key: "gst_registration", label: "Registration", render: (row) => named(REGISTRATIONS, row.gst_registration) },
      { key: "tax_exempt", label: "", width: "6rem", render: (row) => (row.tax_exempt ? "Exempt" : "") },
    ],
    adder: {
      label: "Add the GST registration", permission: "accounting.add_partytaxprofile",
      url: () => "/api/accounting/party-tax-profiles/",
      fields: [
        { key: "gstin", label: "GSTIN", hint: "Its first two digits are the state" },
        { key: "gst_registration", label: "Registration", kind: "choice", choices: REGISTRATIONS },
        { key: "gst_state", label: "State code", hint: "Only for an unregistered party: where it is" },
        { key: "fiscal_position", label: "Fiscal position", kind: "ref", ref: POSITION },
        { key: "tax_exempt", label: "Exempt", kind: "bool" },
        { key: "exemption_reference", label: "Exemption reference", hint: "The certificate, when exempt" },
      ],
      body: (values, party) => ({ ...values, party: party.id }),
    },
  },
];
