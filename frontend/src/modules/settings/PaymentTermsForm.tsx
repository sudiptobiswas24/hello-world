import { RecordScreen } from "../../views/RecordScreen";

/** When an invoice or bill falls due, and any discount for paying early. */
export default function PaymentTermsForm() {
  return (
    <RecordScreen
      endpoint="/api/core/payment-terms/"
      back="/settings/payment-terms"
      backLabel="Payment terms"
      newTitle="New payment term"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "core.add_paymentterms", change: "core.change_paymentterms", delete: "core.delete_paymentterms" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "net_days", label: "Net days", kind: "integer", initial: 30, hint: "Days from document date until the full amount is due" },
        { key: "discount_percent", label: "Discount %", kind: "decimal", places: 2, initial: "0", hint: "Early-settlement discount, e.g. 2.00 for the '2' in 2/10 net 30" },
        { key: "discount_days", label: "Discount days", kind: "integer", initial: 0, hint: "Days within which the discount applies, e.g. the '10' in 2/10 net 30" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
