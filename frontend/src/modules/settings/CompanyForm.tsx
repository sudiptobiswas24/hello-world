import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };

/** Who the company is, the currency its books are kept in, and the accounts documents post to by default. */
export default function CompanyForm() {
  return (
    <RecordScreen
      endpoint="/api/core/company/"
      back="/settings/company"
      backLabel="Company"
      newTitle="New company"
      heading={(row) => String(row.name ?? "")}
      permissions={{ add: "core.add_company", change: "core.change_company", delete: "core.delete_company" }}
      fields={[
        { key: "name", label: "Name" },
        { key: "legal_name", label: "Legal name" },
        { key: "tax_id", label: "GSTIN" },
        { key: "email", label: "Email" },
        { key: "phone", label: "Phone" },
        { key: "website", label: "Website" },
        { key: "bank_name", label: "Bank, for customers to pay into", hint: "Printed on the proforma and the invoice" },
        { key: "bank_account_number", label: "Account number" },
        { key: "bank_ifsc", label: "IFSC" },
        { key: "base_currency", label: "Base currency", kind: "ref", ref: CURRENCY },
        { key: "fiscal_year_start_month", label: "Fiscal year start month", kind: "choice", choices: [["1", "January"], ["2", "February"], ["3", "March"], ["4", "April"], ["5", "May"], ["6", "June"], ["7", "July"], ["8", "August"], ["9", "September"], ["10", "October"], ["11", "November"], ["12", "December"]], initial: 1 },
        { key: "tax_rounding", label: "Tax rounding", kind: "choice", choices: [["line", "Round tax per line"], ["document", "Round tax per document"]], initial: "line", hint: "Some jurisdictions require tax computed on the document total per rate rather than…" },
        { key: "default_inventory_account", label: "Default inventory account", kind: "pick", pick: ACCOUNT, hint: "Asset account for stock value when an item doesn't name its own" },
        { key: "default_cogs_account", label: "Default COGS account", kind: "pick", pick: ACCOUNT, hint: "Cost of goods sold account when an item doesn't name its own" },
        { key: "grni_account", label: "GRNI account", kind: "pick", pick: ACCOUNT, hint: "Goods received not invoiced" },
        { key: "settlement_discount_account", label: "Settlement discount account", kind: "pick", pick: ACCOUNT, hint: "Where early-settlement discounts are written off (an expense)" },
        { key: "bad_debt_account", label: "Bad debt account", kind: "pick", pick: ACCOUNT, hint: "Expense account for receivables judged uncollectable" },
        { key: "default_revenue_account", label: "Default revenue account", kind: "pick", pick: ACCOUNT, hint: "Where a sale is credited when its line names no revenue account" },
        { key: "default_receivable_account", label: "Default receivable account", kind: "pick", pick: ACCOUNT, hint: "What customers owe is booked to this, when an invoice or a receipt names no account of…" },
        { key: "default_payable_account", label: "Default payable account", kind: "pick", pick: ACCOUNT, hint: "What is owed to vendors is booked to this, when a bill or a payment out names no…" },
        { key: "default_bank_account", label: "Default bank account", kind: "pick", pick: ACCOUNT, hint: "The bank a payment goes through when it names none" },
        { key: "default_purchase_expense_account", label: "Default purchase expense account", kind: "pick", pick: ACCOUNT, hint: "Where a non-stocked purchase lands when its line names no account" },
        { key: "fx_gain_account", label: "FX gain account", kind: "pick", pick: ACCOUNT, hint: "Realised exchange gains — when a foreign balance settles for more base currency than…" },
        { key: "fx_loss_account", label: "FX loss account", kind: "pick", pick: ACCOUNT, hint: "Realised exchange losses — when it settles for less" },
        { key: "vendor_prepayment_account", label: "Vendor prepayment account", kind: "pick", pick: ACCOUNT, hint: "Asset account holding money paid to a vendor before the goods arrive" },
        { key: "settlement_discount_received_account", label: "Settlement discount received account", kind: "pick", pick: ACCOUNT, hint: "Where early-settlement discounts taken from vendors are booked (income, or a…" },
        { key: "purchase_price_variance_account", label: "Purchase price variance account", kind: "pick", pick: ACCOUNT, hint: "Where the difference lands when a vendor bills a different price than was agreed on…" },
        { key: "purchase_price_tolerance_percent", label: "Purchase price tolerance %", kind: "decimal", places: 2, initial: "0", hint: "How far above the agreed purchase price a bill may go before it is refused" },
        { key: "net_pay_account", label: "Net pay account", kind: "pick", pick: ACCOUNT, hint: "What payroll owes its staff between posting a pay run and paying it" },
        { key: "customer_deposit_account", label: "Customer deposit account", kind: "pick", pick: ACCOUNT, hint: "Liability account holding money taken up front, before the goods are delivered and the…" },
      ]}
    />
  );
}
