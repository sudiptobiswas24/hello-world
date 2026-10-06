import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const SECTION: FieldDef["ref"] = { endpoint: "/api/accounting/tds-sections/", permission: "accounting.view_tdssection", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const INVOICE: FieldDef["pick"] = { endpoint: "/api/sales/invoices/", permission: "sales.view_invoice", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.customer_name)} · ${String(row.amount_due)} due` };

/** What a customer deducted from paying an invoice, recorded from its remittance and confirmed against Form 26AS. */
export default function CustomerTdsForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/customer-tds/"
      createUrl="/api/sales/customer-tds/record/"
      back="/accounts/customer-tds"
      backLabel="TDS by customers"
      newTitle="TDS a customer deducted"
      heading={(row) => `${String(row.section_code ?? "")} on ${String(row.invoice_number ?? "")}`}
      state={(row) => (row.reversed ? { label: "Reversed", tone: "void" } : row.confirmed_on ? { label: "In 26AS", tone: "done" } : { label: "Not in 26AS yet", tone: "draft" })}
      permissions={{ add: "sales.add_customertds" }}
      fields={[
        { key: "invoice", label: "Invoice", kind: "pick", pick: INVOICE, show: (row) => String(row.invoice_number) },
        { key: "section", label: "Section", kind: "ref", ref: SECTION, show: (row) => String(row.section_code) },
        { key: "amount", label: "Deducted", kind: "money" },
        { key: "date", label: "On", kind: "date", hint: "As the customer's certificate says" },
        { key: "certificate", label: "Form 16A", hint: "Its number, once it comes" },
        { key: "confirmed_on", label: "Found in 26AS on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Found in 26AS", path: "confirm", permission: "sales.change_customertds", primary: true,
          when: (row) => !row.confirmed_on && !row.reversed, done: "Confirmed",
          fields: [{ key: "date", label: "On", kind: "date" }, { key: "certificate", label: "Form 16A" }] },
        { label: "Not in 26AS after all", path: "unconfirm", permission: "sales.change_customertds",
          when: (row) => Boolean(row.confirmed_on), done: "Confirmation cleared" },
        { label: "Reverse", path: "reverse", permission: "sales.change_customertds", danger: true,
          when: (row) => !row.confirmed_on && !row.reversed, done: "Reversed: the invoice is owed that again" },
      ]}
    />
  );
}
