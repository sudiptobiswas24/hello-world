import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { CLAIM_REASONS } from "./claimReasons";

type Row = Record<string, unknown>;

const INVOICE: FieldDef["pick"] = { endpoint: "/api/sales/invoices/", permission: "sales.view_invoice", query: { posted: "true", credits__isnull: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name)} · ${String(row.subtotal ?? row.total)}` };

/**
 * Money given back on an invoice for a customer's claim: a price
 * adjustment, spread over its lines, with their tax. No goods come back
 * and nothing stops being returnable.
 */
export default function ClaimForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/invoices/"
      createUrl="/api/sales/invoices/claim/"
      back="/sales/claims"
      backLabel="Claims"
      newTitle="New claim"
      heading={(row) => String(row.number ?? "")}
      afterCreate={(note) => `/sales/invoices/${String(note.id)}`}
      permissions={{ add: "sales.post_invoice" }}
      fields={[
        { key: "invoice", label: "On invoice", kind: "pick", pick: INVOICE },
        { key: "reason", label: "For", kind: "choice", choices: CLAIM_REASONS },
        { key: "net", label: "Given back, before tax", kind: "money", hint: "The tax the invoice bore follows in proportion" },
        { key: "date", label: "On", kind: "date" },
        { key: "memo", label: "Memo" },
      ]}
    />
  );
}
