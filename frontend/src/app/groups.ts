/**
 * Each module's screens in menus, in the order the work runs: the documents
 * first, then what is reported from them, then what is set up once.
 *
 * Sales offered 33 screens in one row of tabs, Accounts 37 (O392). Every
 * screen a module registers is in exactly one group here; apps/web/tests.py
 * (EveryScreenIsInOneGroupTests) holds that. A screen this list misses would
 * still be offered, under "Other", and the test says which.
 */
export const GROUPS: Record<string, [string, string[]][]> = {
  sales: [
    ["Orders", ["quotations", "orders", "deliveries", "unacknowledged", "releases", "call-offs", "supplied-items"]],
    ["Invoicing", ["invoices", "receipts", "recurring", "variation-bills", "reminders", "claims", "bad-debts"]],
    ["Customers", ["customers", "leads", "opportunities", "pipeline", "board", "activities", "campaigns"]],
    ["Pricing", ["price-lists", "price-indices", "price-clauses"]],
    ["Team", ["reps", "teams", "targets", "commission-plans", "commission"]],
    ["Reporting", ["aging", "revenue", "targets-report"]],
    ["Configuration", ["reminder-levels"]],
  ],
  purchasing: [
    ["Orders", ["requisitions", "to-quote", "rfqs", "orders", "blanket-orders", "reorder", "rate-contracts", "rate-differences"]],
    ["Receiving", ["goods-in", "receive-by-scan", "weight-claims", "unbilled-freight"]],
    ["Bills", ["bills", "payments", "bills-of-entry"]],
    ["Vendors", ["vendors", "vendor-prices"]],
    ["Reporting", ["aging", "vendor-scorecard"]],
    ["Configuration", ["reorder-rules", "budgets", "approval-policies", "customs-valuation"]],
  ],
  production: [
    ["Shop floor", ["work-orders", "board", "schedule", "gantt", "issues", "output", "time", "bales", "rolls", "rebatches"]],
    ["Planning", ["plan", "planned", "plan-actions", "planned-for", "promise", "master-schedule", "forecasts",
      "forecast-from-history", "shipment-history", "levels"]],
    ["Reporting", ["station-report", "operator-yield", "scrap", "step-counts", "profitability"]],
    ["Configuration", ["planning-settings", "routes"]],
  ],
  making: [
    ["Specifications", ["bags", "fabrics", "tapes", "films", "liners", "designs"]],
    ["Bills and routings", ["boms", "change-orders", "routings"]],
    ["Costing", ["cost-sheets", "cost-versions", "material-rates", "stage-rates", "quote-policies"]],
    ["Floor setup", ["work-centres", "machines", "shifts", "crews", "tools", "setup-families", "changeovers"]],
    ["Configuration", ["manufacturing-settings", "scrap-reasons", "stoppage-reasons"]],
  ],
  plant: [
    ["Production", ["daily", "losses", "hours-lost", "stoppages"]],
    ["Maintenance", ["due", "calendar", "jobs", "breakdowns", "schedules", "critical-spares"]],
    ["Energy", ["energy", "readings", "meters", "tariffs"]],
  ],
  quality: [
    ["Inspection", ["inspections", "awaiting-decision", "batch-status", "supplier-certificates", "sampling"]],
    ["Issues", ["alerts", "complaints"]],
    ["Analysis", ["control-chart", "certificates"]],
    ["Calibration", ["calibration-due", "instruments", "calibrations"]],
    ["Configuration", ["plans", "characteristics", "quality-settings"]],
  ],
  accounts: [
    ["Ledger", ["journals", "chart", "recurring-journals", "bank-statements", "bank-import", "cost-centres"]],
    ["GST", ["gst", "gstr2b", "e-invoices", "eway-bills", "where-goods-went", "ports", "self-invoices", "payment-vouchers", "gst-challans",
      "gst-cash-ledger"]],
    ["TDS and TCS", ["tds-deducted", "tds-challans", "tds-return", "customer-tds", "tcs-collected", "tcs-challans",
      "tcs-return", "tcs-declarations"]],
    ["Assets", ["assets", "asset-register", "tax-depreciation", "asset-categories"]],
    ["Period end", ["periods", "restatements", "budgets", "bad-debts"]],
    ["Reporting", ["trial-balance", "profit-and-loss", "balance-sheet", "costs-by-centre", "budget-report",
      "bank-stock-statement", "msme-payments"]],
  ],
  payroll: [
    ["Employees", ["employees", "attendance", "attendance-gaps", "punch-file", "leave", "timesheets", "claims", "appraisals",
      "departments"]],
    ["Pay", ["runs", "my-payslips", "pay-details", "salary-files", "advances", "owed-back", "gratuity"]],
    ["Statutory", ["dues", "remittances", "registers", "statutory-figures", "minimum-wages", "labour-contractors"]],
    ["Recruitment", ["openings", "applicants"]],
    ["Configuration", ["pay-components", "leave-policies"]],
  ],
  stores: [
    ["Stock", ["on-hand", "movements", "reserved", "batches"]],
    ["Operations", ["scan", "picking", "transfers", "adjustments", "counts", "job-work", "customer-material",
      "customer-material-back"]],
    ["Gate", ["gate", "gate-passes", "weighings"]],
    ["Items", ["items", "item-templates", "item-attributes"]],
    ["Reporting", ["value", "against-books", "slow-moving", "negative"]],
    ["Configuration", ["warehouses", "bins", "reasons"]],
  ],
  settings: [
    ["People", ["logins", "role-proposals"]],
    ["Company", ["company", "numbering", "plant-gst", "books-opening", "gst-registrations", "licences", "luts", "gst-returns-filed", "custom-fields"]],
    ["Taxes", ["taxes", "tax-groups", "fiscal-positions", "tds-sections", "tds-rates", "tcs-sections", "tcs-rates"]],
    ["Masters", ["currencies", "exchange-rates", "countries", "units", "payment-terms", "party-tags", "charge-types"]],
    ["System", ["health", "edit-log", "problems", "import", "seeded-defaults"]],
  ],
};
