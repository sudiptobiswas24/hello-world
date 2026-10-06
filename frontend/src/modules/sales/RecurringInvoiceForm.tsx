import { Lines, type TradeLine } from "../../forms/Lines";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { ACCOUNT, CUSTOMER, EMPLOYEE_PARTY } from "./extraRefs";

type Row = Record<string, unknown>;

const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };
const TERMS: FieldDef["ref"] = { endpoint: "/api/core/payment-terms/", permission: "core.view_paymentterms", label: (row: Row) => String(row.name) };

/**
 * The same invoice to the same customer every interval: rent, a service
 * contract. Each one issued is an ordinary invoice, a draft unless the
 * schedule posts it.
 */
export default function RecurringInvoiceForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/recurring-invoices/"
      back="/sales/recurring"
      backLabel="Recurring invoices"
      newTitle="New recurring invoice"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.customer_name ?? "")}`}
      state={(row) => (row.is_active === false ? { label: "Stopped", tone: "draft" } : { label: "Running", tone: "open" })}
      permissions={{ add: "sales.add_recurringinvoice", change: "sales.change_recurringinvoice", delete: "sales.delete_recurringinvoice" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER, createOnly: true, show: (row) => String(row.customer_name ?? "") },
        { key: "receivable_account", label: "Receivable account", kind: "pick", pick: ACCOUNT },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY, hint: "The customer's, if left empty" },
        { key: "payment_terms", label: "Payment terms", kind: "ref", ref: TERMS, hint: "The customer's, if left empty" },
        { key: "sales_rep", label: "Sales rep", kind: "pick", pick: EMPLOYEE_PARTY },
        { key: "interval", label: "Every", kind: "choice", choices: [["weekly", "Week"], ["monthly", "Month"], ["quarterly", "Quarter"], ["yearly", "Year"]], initial: "monthly" },
        { key: "interval_count", label: "Intervals between", kind: "integer", initial: 1, hint: "2 with Month: every other month" },
        { key: "start_date", label: "Starts", kind: "date" },
        { key: "end_date", label: "Ends", kind: "date", hint: "Empty: until stopped" },
        { key: "next_run_date", label: "Next invoice", readOnly: true, kind: "date" },
        { key: "auto_post", label: "Post each invoice", kind: "bool", hint: "Otherwise each is left a draft to check" },
        { key: "is_active", label: "Running", kind: "bool", initial: true },
      ]}
      actions={[
        { label: "Issue the next invoice", path: "generate", permission: "sales.add_invoice", primary: true,
          when: (row) => Boolean(row.is_active), done: "Invoice issued", then: (invoice) => `/sales/invoices/${String(invoice.id)}` },
      ]}
      below={(schedule, editable) => (
        <section className="related">
          <h2 className="section-title">What each invoice bills</h2>
          <Lines lines={(schedule.lines as TradeLine[]) ?? []} endpoint="/api/sales/recurring-invoice-lines/"
            parent="schedule" parentId={Number(schedule.id)} editable={editable} />
        </section>
      )}
    />
  );
}
