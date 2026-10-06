import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const FISCALPOSITION: FieldDef["ref"] = { endpoint: "/api/accounting/fiscal-positions/", permission: "accounting.view_fiscalposition", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A party's GST registration: what an e-invoice, GSTR-1 and the place of supply read. */
export default function GstRegistrationForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/party-tax-profiles/"
      back="/settings/gst-registrations"
      backLabel="GST registrations"
      newTitle="New GST registration"
      heading={(row) => String(row.gstin ?? "")}
      permissions={{ add: "accounting.add_partytaxprofile", change: "accounting.change_partytaxprofile", delete: "accounting.delete_partytaxprofile" }}
      fields={[
        { key: "party", label: "Party", kind: "pick", pick: PARTY },
        { key: "fiscal_position", label: "Fiscal position", kind: "ref", ref: FISCALPOSITION },
        { key: "tax_exempt", label: "Tax exempt", kind: "bool", initial: false },
        { key: "exemption_reference", label: "Exemption reference", hint: "Certificate or registration number for the exemption" },
        { key: "gstin", label: "GSTIN", hint: "The party's GST registration, checked character by character" },
        { key: "gst_state", label: "GST state", hint: "Where the party is, as a GST state code" },
        { key: "gst_registration", label: "GST registration", kind: "choice", choices: [["regular", "Registered, regular"], ["composition", "Registered, composition"], ["sez", "Special economic zone"], ["unregistered", "Unregistered"], ["overseas", "Overseas"]] },
      ]}
    />
  );
}
