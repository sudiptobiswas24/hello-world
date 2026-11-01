import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => row.status === "draft";
const posted = (row: Row) => row.status === "posted";

const PARTY = { endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" },
  label: (row: Row) => String(row.name) };
const ACCOUNT = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const CURRENCY = { endpoint: "/api/core/currencies/", permission: "core.view_currency", 
  label: (row: Row) => String(row.code) };
const RECEIPT = { endpoint: "/api/purchasing/goods-receipts/", permission: "purchasing.view_goodsreceipt",
  query: { posted: "true", reverses__isnull: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.vendor_name ?? "")}` };
const BILL = { endpoint: "/api/purchasing/bills/", permission: "purchasing.view_bill", query: { posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.vendor_name ?? "")} · ${String(row.reference ?? "")}` };
const ENTRY = { endpoint: "/api/purchasing/bills-of-entry/", permission: "purchasing.view_billofentry",
  query: { status: "posted" }, label: (row: Row) => `${String(row.number)} · BE ${String(row.be_number)}` };

/** A bill of entry: the goods it cleared, valued at the customs rate, its duty and IGST, posted or voided. */
export default function BillOfEntryForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/bills-of-entry/"
      back="/purchasing/bills-of-entry"
      backLabel="Bills of entry"
      newTitle="New bill of entry"
      heading={(row) => `${String(row.number || "Draft")} · BE ${String(row.be_number ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: posted(row) ? "done" : draft(row) ? "open" : "draft" })}
      permissions={{ add: "purchasing.add_billofentry", change: "purchasing.change_billofentry",
        delete: "purchasing.delete_billofentry" }}
      editable={draft}
      fields={[
        { key: "be_number", label: "BE number" },
        { key: "be_date", label: "BE date", kind: "date" },
        { key: "port_code", label: "Port code", hint: "Six characters, INCCU1" },
        { key: "currency", label: "Invoice currency", kind: "pick", pick: CURRENCY, show: (row: Row) => String(row.currency_code || "—") },
        { key: "customs_rate", label: "Customs exchange rate", kind: "decimal", places: 4, hint: "CBIC's notified rate, as the BE states it" },
        { key: "freight", label: "Freight", kind: "decimal", hint: "In the invoice currency; blank if not known (20% of FOB is taken)" },
        { key: "insurance", label: "Insurance", kind: "decimal", hint: "Blank if not known (1.125% of FOB is taken)" },
        { key: "bcd_rate", label: "Basic customs duty %", kind: "decimal", initial: "7.5" },
        { key: "sws_rate", label: "Social welfare surcharge % of duty", kind: "decimal", initial: "10" },
        { key: "igst_rate", label: "IGST %", kind: "decimal", initial: "18" },
        { key: "basis", label: "Land the duty by", kind: "choice", choices: [["value", "Value"], ["quantity", "Quantity"], ["weight", "Weight (kg)"]], initial: "value" },
        { key: "customs_party", label: "Duty paid to", kind: "pick", pick: PARTY, show: (row: Row) => String(row.customs_party_name || "—") },
        { key: "payable_account", label: "Customs duty payable", kind: "pick", pick: ACCOUNT, show: (row: Row) => String(row.payable_account_name || "—") },
        { key: "duty_account", label: "Customs duty account", kind: "pick", pick: ACCOUNT, show: (row: Row) => String(row.duty_account_name || "—") },
        { key: "igst_account", label: "Input IGST account", kind: "pick", pick: ACCOUNT, show: (row: Row) => String(row.igst_account_name || "—") },
        { key: "supersedes", label: "Re-assesses", kind: "pick", pick: ENTRY, show: (row: Row) => String(row.supersedes_number || "—"), createOnly: true, hint: "Only for a re-assessment of a posted BE" },
        { key: "valuation_rule", label: "Valuation", kind: "text", readOnly: true, existingOnly: true, wide: true },
        { key: "freight_taken", label: "Freight taken", kind: "decimal", readOnly: true, existingOnly: true },
        { key: "insurance_taken", label: "Insurance taken", kind: "decimal", readOnly: true, existingOnly: true },
        { key: "assessable_total", label: "Assessable value", kind: "money", readOnly: true, existingOnly: true },
        { key: "bcd_total", label: "Basic customs duty", kind: "money", readOnly: true, existingOnly: true },
        { key: "sws_total", label: "Social welfare surcharge", kind: "money", readOnly: true, existingOnly: true },
        { key: "igst_total", label: "IGST", kind: "money", readOnly: true, existingOnly: true },
        { key: "duty_bill_number", label: "Duty bill", kind: "text", readOnly: true, existingOnly: true },
        { key: "void_note_number", label: "Voided by", kind: "text", readOnly: true, existingOnly: true },
        { key: "void_reason", label: "Why voided", kind: "text", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "purchasing.post_billofentry", primary: true, when: draft, done: "Posted" },
        { label: "Void", path: "void", permission: "purchasing.post_billofentry", danger: true, when: posted, done: "Voided",
          fields: [{ key: "reason", label: "Why", kind: "text" }, { key: "on_date", label: "On", kind: "date" }] },
      ]}
      links={[{ label: "Print", href: (row) => `/api/purchasing/bills-of-entry/${String(row.id)}/document/` }]}
      panels={[{
        title: "Goods cleared", permission: "purchasing.view_billofentryline", endpoint: "/api/purchasing/bill-of-entry-lines/",
        query: (row) => ({ entry: String(row.id) }),
        columns: [
          { key: "receipt_number", label: "Receipt", width: "10rem" },
          { key: "item_name", label: "Goods" },
          { key: "quantity", label: "Qty", kind: "quantity", width: "7rem" },
          { key: "fob", label: "FOB", kind: "quantity", width: "8rem" },
          { key: "assessable_value", label: "Assessable", kind: "money", width: "9rem" },
          { key: "bcd", label: "BCD", kind: "money", width: "8rem" },
          { key: "sws", label: "SWS", kind: "money", width: "7rem" },
          { key: "igst", label: "IGST", kind: "money", width: "8rem" },
        ],
        adder: { label: "Clear a receipt", permission: "purchasing.change_billofentry", when: draft,
          url: (row) => `/api/purchasing/bills-of-entry/${String(row.id)}/clear_receipt/`,
          fields: [{ key: "receipt", label: "Receipt", kind: "pick", pick: RECEIPT },
            { key: "bill", label: "Supplier's bill", kind: "pick", pick: BILL }],
          body: (values) => values },
        remover: { permission: "purchasing.delete_billofentryline", url: (line) => `/api/purchasing/bill-of-entry-lines/${String(line.id)}/`, when: draft },
      }]}
    />
  );
}
