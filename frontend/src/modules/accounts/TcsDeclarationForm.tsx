import { RecordScreen } from "../../views/RecordScreen";
import { PARTY, TCS_SECTION } from "./refs";

/** A buyer's declaration that exempts its purchases from TCS for the days it covers. Kept as invoices used it. */
export default function TcsDeclarationForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tcs-declarations/"
      back="/accounts/tcs-declarations"
      backLabel="TCS declarations"
      newTitle="New declaration"
      heading={(row) => `Form ${String(row.form ?? "")} · ${String(row.party_name ?? "")}`}
      permissions={{ add: "accounting.add_tcsdeclaration", change: "accounting.change_tcsdeclaration" }}
      fields={[
        { key: "party", label: "Buyer", kind: "pick", pick: PARTY, show: (row) => String(row.party_name) },
        { key: "section", label: "Section", kind: "ref", ref: TCS_SECTION },
        { key: "form", label: "Form", existingOnly: true, readOnly: true, hint: "Named by the day it was received" },
        { key: "pan", label: "Buyer's PAN", hint: "As the declaration gives it; without one it exempts nothing" },
        { key: "reference", label: "Reference" },
        { key: "received_on", label: "Received on", kind: "date" },
        { key: "valid_from", label: "Covers from", kind: "date" },
        { key: "valid_to", label: "Covers to", kind: "date", hint: "Usually the financial year's last day" },
      ]}
    />
  );
}
