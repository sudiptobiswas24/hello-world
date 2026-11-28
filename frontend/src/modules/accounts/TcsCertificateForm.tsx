import { RecordScreen } from "../../views/RecordScreen";
import { PARTY, TCS_SECTION } from "./refs";

/** A buyer's certificate for TCS at a lower rate, up to its amount. Kept as invoices used it. */
export default function TcsCertificateForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tcs-certificates/"
      back="/accounts/tcs-certificates"
      backLabel="TCS certificates"
      newTitle="New certificate"
      heading={(row) => `${String(row.reference ?? "")} · ${String(row.party_name ?? "")}`}
      permissions={{ add: "accounting.add_tcscertificate", change: "accounting.change_tcscertificate" }}
      fields={[
        { key: "party", label: "Buyer", kind: "pick", pick: PARTY, show: (row) => String(row.party_name) },
        { key: "section", label: "Section", kind: "ref", ref: TCS_SECTION },
        { key: "reference", label: "Certificate number", hint: "As the officer issued it (section 206C(9))" },
        { key: "rate_percent", label: "Rate %", kind: "decimal", places: 4, hint: "The rate it allows, nil included" },
        { key: "amount", label: "Up to", kind: "decimal", places: 2, hint: "The value its rate applies to; past it the section's rate does" },
        { key: "left", label: "Left", kind: "money", existingOnly: true, readOnly: true, hint: "Less what invoices were collected on under it, and what credit notes gave back" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date", hint: "In the financial year it was issued for" },
      ]}
    />
  );
}
