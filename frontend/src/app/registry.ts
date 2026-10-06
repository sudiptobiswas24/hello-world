import type { ComponentType } from "react";

type Loader = () => Promise<{ default: ComponentType }>;

/**
 * Every screen the application has, in one list: the navigation, the
 * routes and the command palette all read it, so a screen cannot be
 * reachable from one and missing from another.
 *
 * `permission` is the one the server checks to read what the screen
 * shows; a screen is offered to whoever holds it. `detail` is the screen
 * for one record (`path/:id`), and for a new one (`path/new`) to whoever
 * holds `create`.
 */
export interface Screen {
  path: string; // under the module, e.g. "invoices"
  label: string;
  permission: string;
  keywords?: string;
  load: Loader;
  detail?: Loader;
  create?: string;
  /** Reached from other screens, not offered in the navigation. */
  hidden?: boolean;
  /** Further permissions the screen's other reads need (its detail, a panel). */
  also?: string[];
}

/** Whether to offer a screen: what it reads first, and everything else it needs. */
export function offered(screen: Screen, can: (permission: string) => boolean): boolean {
  return !screen.hidden && can(screen.permission) && (screen.also ?? []).every(can);
}

export interface Module {
  key: string;
  label: string;
  icon: IconName;
  screens: Screen[];
}

export type IconName = "home" | "sales" | "purchasing" | "stock" | "production" | "making" | "plant" | "quality" | "accounts" | "people";

export const MODULES: Module[] = [
  {
    key: "sales",
    label: "Sales",
    icon: "sales",
    screens: [
      {
        path: "orders", label: "Orders", permission: "sales.view_salesorder", create: "sales.add_salesorder",
        keywords: "so sales order customer", load: () => import("../modules/sales/Orders"),
        detail: () => import("../modules/sales/OrderForm"),
      },
      {
        path: "quotations", label: "Quotations", permission: "sales.view_quotation", create: "sales.add_quotation",
        keywords: "quote offer estimate", load: () => import("../modules/sales/Quotations"),
        detail: () => import("../modules/sales/QuotationForm"),
      },
      {
        path: "deliveries", label: "Deliveries", permission: "sales.view_delivery",
        keywords: "dispatch ship challan returns", load: () => import("../modules/sales/Deliveries"),
        detail: () => import("../modules/sales/DeliveryForm"),
      },
      {
        path: "invoices", label: "Invoices", permission: "sales.view_invoice", create: "sales.add_invoice",
        keywords: "bill credit note customer", load: () => import("../modules/sales/Invoices"),
        detail: () => import("../modules/sales/InvoiceForm"),
      },
      {
        path: "receipts", label: "Money received", permission: "accounting.view_payment", create: "accounting.add_payment",
        keywords: "payment receipt cheque collection", load: () => import("../modules/sales/Receipts"),
        detail: () => import("../modules/sales/ReceiptForm"),
      },
      {
        path: "customers", label: "Customers", permission: "core.view_party", create: "core.add_party",
        keywords: "party buyer", load: () => import("../modules/sales/Customers"),
        detail: () => import("../modules/sales/CustomerForm"),
      },
      {
        path: "aging", label: "Receivables by age", permission: "sales.view_invoice",
        keywords: "aging overdue outstanding debtors", load: () => import("../modules/sales/Aging"),
      },
      {
        path: "revenue", label: "Sales report", permission: "sales.view_invoice",
        keywords: "revenue turnover report", load: () => import("../modules/sales/Revenue"),
      },
    ],
  },
  {
    key: "purchasing",
    label: "Purchasing",
    icon: "purchasing",
    screens: [
      {
        path: "orders", label: "Purchase orders", permission: "purchasing.view_purchaseorder", create: "purchasing.add_purchaseorder",
        keywords: "po buy vendor supplier", load: () => import("../modules/purchasing/Orders"),
        detail: () => import("../modules/purchasing/OrderForm"),
      },
      {
        path: "goods-in", label: "Goods in", permission: "purchasing.view_goodsreceipt",
        keywords: "grn receipt receive inward batch", load: () => import("../modules/purchasing/GoodsReceipts"),
        detail: () => import("../modules/purchasing/GoodsReceiptForm"),
      },
      {
        path: "bills", label: "Bills", permission: "purchasing.view_bill", create: "purchasing.add_bill",
        keywords: "vendor invoice debit note payable", load: () => import("../modules/purchasing/Bills"),
        detail: () => import("../modules/purchasing/BillForm"),
      },
      {
        path: "payments", label: "Money paid", permission: "accounting.view_payment", create: "accounting.add_payment",
        keywords: "payment disbursement cheque neft vendor", load: () => import("../modules/purchasing/Payments"),
        detail: () => import("../modules/purchasing/PaymentForm"),
      },
      {
        path: "vendors", label: "Vendors", permission: "core.view_party", create: "core.add_party",
        keywords: "supplier party", load: () => import("../modules/purchasing/Vendors"),
        detail: () => import("../modules/purchasing/VendorForm"),
      },
      {
        path: "aging", label: "Payables by age", permission: "purchasing.view_purchaseorder",
        keywords: "aging overdue creditors payable", load: () => import("../modules/purchasing/Aging"),
      },
    ],
  },
  {
    key: "production",
    label: "Production",
    icon: "production",
    screens: [
      {
        path: "plan", label: "Plan", permission: "planning.view_planningrun",
        keywords: "mrp planning run shortage", load: () => import("../modules/production/Plan"),
        detail: () => import("../modules/production/RunForm"),
      },
      {
        path: "planned", label: "Planned orders", permission: "planning.view_plannedorder",
        keywords: "suggestion firm", load: () => import("../modules/production/PlannedOrders"),
      },
      {
        path: "work-orders", label: "Work orders", permission: "manufacturing.view_workorder",
        keywords: "run job production", load: () => import("../modules/production/WorkOrders"),
        detail: () => import("../modules/production/WorkOrderForm"),
      },
      {
        path: "schedule", label: "Machine schedule", permission: "manufacturing.view_workorder",
        keywords: "dispatch loom extruder queue", load: () => import("../modules/production/Schedule"),
      },
      {
        path: "issues", label: "Material issues", permission: "manufacturing.view_materialissue",
        create: "manufacturing.add_materialissue", also: ["manufacturing.view_workorder"],
        keywords: "issue material to run return store consume", load: () => import("../modules/floor/Issues"),
        detail: () => import("../modules/floor/IssueForm"),
      },
      {
        path: "output", label: "Output", permission: "manufacturing.view_productionentry",
        create: "manufacturing.add_productionentry", also: ["manufacturing.view_workorder"],
        keywords: "production entry made good scrap", load: () => import("../modules/floor/Entries"),
        detail: () => import("../modules/floor/EntryForm"),
      },
      {
        path: "time", label: "Time booked", permission: "manufacturing.view_timebooking",
        create: "manufacturing.add_timebooking", also: ["manufacturing.view_workorderoperation"],
        keywords: "hours minutes labour machine booking", load: () => import("../modules/floor/Bookings"),
        detail: () => import("../modules/floor/BookingForm"),
      },
      {
        path: "promise", label: "When can we promise?", permission: "planning.view_plannedorder",
        also: ["inventory.view_warehouse"],
        keywords: "atp ctp delivery date available to promise", load: () => import("../modules/production/WhenCanWePromise"),
      },
      {
        path: "forecasts", label: "Forecasts", permission: "planning.view_forecast", create: "planning.add_forecast",
        keywords: "demand expected sales", load: () => import("../modules/production/Forecasts"),
        detail: () => import("../modules/production/ForecastForm"),
      },
      {
        path: "forecast-from-history", label: "Forecast from history", permission: "planning.view_forecast",
        also: ["inventory.view_warehouse"],
        keywords: "seasonal statistical propose backtest", load: () => import("../modules/production/Propose"),
      },
      {
        path: "master-schedule", label: "Master schedule", permission: "planning.view_masterscheduleentry",
        create: "planning.add_masterscheduleentry",
        keywords: "mps build ahead season campaign", load: () => import("../modules/production/MasterSchedule"),
        detail: () => import("../modules/production/MasterScheduleForm"),
      },
      {
        path: "routes", label: "Transfer routes", permission: "planning.view_transferroute", create: "planning.add_transferroute",
        keywords: "branch depot restock", load: () => import("../modules/production/Routes"),
        detail: () => import("../modules/production/RouteForm"),
      },
    ],
  },
  {
    key: "making",
    label: "How it's made",
    icon: "making",
    screens: [
      {
        path: "boms", label: "Bills of materials", permission: "manufacturing.view_billofmaterials",
        create: "manufacturing.add_billofmaterials",
        keywords: "bom recipe formula inputs components", load: () => import("../modules/making/Boms"),
        detail: () => import("../modules/making/BomForm"),
      },
      {
        path: "routings", label: "Routings", permission: "manufacturing.view_routing",
        create: "manufacturing.add_routing",
        keywords: "steps operations process route", load: () => import("../modules/making/Routings"),
        detail: () => import("../modules/making/RoutingForm"),
      },
      {
        path: "work-centres", label: "Work centres", permission: "manufacturing.view_workcentre",
        create: "manufacturing.add_workcentre",
        keywords: "bank looms extruders capacity rates cost an hour", load: () => import("../modules/making/WorkCentres"),
        detail: () => import("../modules/making/WorkCentreForm"),
      },
      {
        path: "machines", label: "Machines", permission: "manufacturing.view_machine",
        create: "manufacturing.add_machine",
        keywords: "loom extruder press capability width", load: () => import("../modules/making/Machines"),
        detail: () => import("../modules/making/MachineForm"),
      },
      {
        path: "shifts", label: "Shifts", permission: "manufacturing.view_shift",
        create: "manufacturing.add_shift",
        keywords: "hours day night", load: () => import("../modules/making/Shifts"),
        detail: () => import("../modules/making/ShiftForm"),
      },
      {
        path: "crews", label: "Crews", permission: "manufacturing.view_crewassignment",
        create: "manufacturing.add_crewassignment",
        also: ["hr.view_employee"],
        keywords: "manning operators who shift bank", load: () => import("../modules/making/Crews"),
        detail: () => import("../modules/making/CrewForm"),
      },
      {
        path: "tools", label: "Tools", permission: "manufacturing.view_tool",
        create: "manufacturing.add_tool",
        keywords: "cylinder die reed screen wear life", load: () => import("../modules/making/Tools"),
        detail: () => import("../modules/making/ToolForm"),
      },
      {
        path: "setup-families", label: "Setup families", permission: "manufacturing.view_setupfamily",
        create: "manufacturing.add_setupfamily",
        keywords: "changeover colour family", load: () => import("../modules/making/SetupFamilies"),
        detail: () => import("../modules/making/SetupFamilyForm"),
      },
      {
        path: "changeovers", label: "Changeover rules", permission: "manufacturing.view_changeoverrule",
        create: "manufacturing.add_changeoverrule",
        keywords: "changeover setup minutes purge", load: () => import("../modules/making/ChangeoverRules"),
        detail: () => import("../modules/making/ChangeoverRuleForm"),
      },
      {
        path: "scrap-reasons", label: "Scrap reasons", permission: "manufacturing.view_scrapreason",
        create: "manufacturing.add_scrapreason",
        keywords: "waste why", load: () => import("../modules/making/ScrapReasons"),
        detail: () => import("../modules/making/ScrapReasonForm"),
      },
      {
        path: "stoppage-reasons", label: "Stoppage reasons", permission: "manufacturing.view_downtimereason",
        create: "manufacturing.add_downtimereason",
        keywords: "downtime breakdown why", load: () => import("../modules/making/DowntimeReasons"),
        detail: () => import("../modules/making/DowntimeReasonForm"),
      },
    ],
  },
  {
    key: "plant",
    label: "Plant",
    icon: "plant",
    screens: [
      {
        path: "losses", label: "Machine effectiveness", permission: "manufacturing.view_workcentre",
        keywords: "oee availability performance quality losses productivity", load: () => import("../modules/plant/Losses"),
      },
      {
        path: "hours-lost", label: "Where the hours went", permission: "manufacturing.view_downtime",
        keywords: "downtime reasons pareto stoppages", load: () => import("../modules/plant/HoursLost"),
      },
      {
        path: "stoppages", label: "Stoppages", permission: "manufacturing.view_downtime", create: "manufacturing.add_downtime",
        keywords: "downtime breakdown stop", load: () => import("../modules/plant/Stoppages"),
        detail: () => import("../modules/plant/StoppageForm"),
      },
      {
        path: "due", label: "Maintenance due", permission: "manufacturing.view_maintenanceschedule",
        keywords: "preventive service overdue", load: () => import("../modules/plant/Due"),
      },
      {
        path: "jobs", label: "Maintenance jobs", permission: "manufacturing.view_maintenancejob", create: "manufacturing.add_maintenancejob",
        keywords: "repair breakdown fitter spares work order", load: () => import("../modules/plant/Jobs"),
        detail: () => import("../modules/plant/JobForm"),
      },
      {
        path: "schedules", label: "Maintenance schedules", permission: "manufacturing.view_maintenanceschedule",
        create: "manufacturing.add_maintenanceschedule",
        keywords: "preventive routine running hours calendar", load: () => import("../modules/plant/Schedules"),
        detail: () => import("../modules/plant/ScheduleForm"),
      },
      {
        path: "breakdowns", label: "Breakdowns", permission: "manufacturing.view_maintenancejob",
        keywords: "reliability mtbf mttr failures", load: () => import("../modules/plant/Reliability"),
      },
      {
        path: "energy", label: "Electricity", permission: "manufacturing.view_energymeter",
        keywords: "kwh power energy idle units", load: () => import("../modules/plant/Energy"),
      },
      {
        path: "readings", label: "Meter readings", permission: "manufacturing.view_meterreading", create: "manufacturing.add_meterreading",
        keywords: "electricity meter reading kwh", load: () => import("../modules/plant/Readings"),
        detail: () => import("../modules/plant/ReadingForm"),
      },
      {
        path: "meters", label: "Energy meters", permission: "manufacturing.view_energymeter", create: "manufacturing.add_energymeter",
        keywords: "electricity meter", load: () => import("../modules/plant/Meters"),
        detail: () => import("../modules/plant/MeterForm"),
      },
      {
        path: "tariffs", label: "Electricity tariffs", permission: "manufacturing.view_energytariff", create: "manufacturing.add_energytariff",
        keywords: "rate per unit kwh", load: () => import("../modules/plant/Tariffs"),
        detail: () => import("../modules/plant/TariffForm"),
      },
    ],
  },
  {
    key: "quality",
    label: "Quality",
    icon: "quality",
    screens: [
      {
        path: "inspections", label: "Inspections", permission: "quality.view_inspection", create: "quality.add_inspection",
        keywords: "qc test batch release hold", load: () => import("../modules/quality/Inspections"),
        detail: () => import("../modules/quality/InspectionForm"),
      },
      {
        path: "batch-status", label: "Batch status", permission: "quality.view_inspection", also: ["inventory.view_lot"],
        keywords: "released held lot status", load: () => import("../modules/quality/BatchStatus"),
      },
      {
        path: "complaints", label: "Complaints", permission: "manufacturing.view_complaint", create: "manufacturing.add_complaint",
        keywords: "customer complaint capa corrective action", load: () => import("../modules/quality/Complaints"),
        detail: () => import("../modules/quality/ComplaintForm"),
      },
      {
        path: "control-chart", label: "Control chart", permission: "quality.view_inspection",
        also: ["quality.view_inspectionplan", "quality.view_characteristic"],
        keywords: "spc xbar capability cpk", load: () => import("../modules/quality/Spc"),
      },
      {
        path: "sampling", label: "Sampling", permission: "quality.view_inspection",
        keywords: "aql iso 2859 sample size", load: () => import("../modules/quality/Sampling"),
      },
      {
        path: "plans", label: "Inspection plans", permission: "quality.view_inspectionplan", create: "quality.add_inspectionplan",
        keywords: "limits specification tests", load: () => import("../modules/quality/Plans"),
        detail: () => import("../modules/quality/PlanForm"),
      },
      {
        path: "characteristics", label: "Characteristics", permission: "quality.view_characteristic",
        create: "quality.add_characteristic",
        keywords: "tenacity gsm mfi elongation", load: () => import("../modules/quality/Characteristics"),
        detail: () => import("../modules/quality/CharacteristicForm"),
      },
      {
        path: "calibration-due", label: "Calibration due", permission: "quality.view_instrument",
        keywords: "overdue instruments gauges", load: () => import("../modules/quality/CalibrationDue"),
      },
      {
        path: "instruments", label: "Instruments", permission: "quality.view_instrument", create: "quality.add_instrument",
        keywords: "scale tester gauge", load: () => import("../modules/quality/Instruments"),
        detail: () => import("../modules/quality/InstrumentForm"),
      },
      {
        path: "calibrations", label: "Calibrations", permission: "quality.view_calibration", create: "quality.add_calibration",
        keywords: "certificate standard", load: () => import("../modules/quality/Calibrations"),
        detail: () => import("../modules/quality/CalibrationForm"),
      },
    ],
  },
  {
    key: "accounts",
    label: "Accounts",
    icon: "accounts",
    screens: [
      {
        // The list reads the chart; each account opens on its ledger, which
        // is the books and takes the right to read journal entries.
        path: "chart", label: "Chart of accounts", permission: "accounting.view_account",
        also: ["accounting.view_journalentry"],
        keywords: "ledger account gl", load: () => import("../modules/accounts/Chart"),
        detail: () => import("../modules/accounts/Ledger"),
      },
      {
        path: "journals", label: "Journal entries", permission: "accounting.view_journalentry", create: "accounting.add_journalentry",
        keywords: "journal voucher jv", load: () => import("../modules/accounts/Journals"),
        detail: () => import("../modules/accounts/JournalForm"),
      },
      {
        path: "trial-balance", label: "Trial balance", permission: "accounting.view_journalentry",
        keywords: "tb balances", load: () => import("../modules/accounts/TrialBalance"),
      },
      {
        path: "profit-and-loss", label: "Profit and loss", permission: "accounting.view_journalentry",
        keywords: "p&l income statement", load: () => import("../modules/accounts/ProfitLoss"),
      },
      {
        path: "balance-sheet", label: "Balance sheet", permission: "accounting.view_journalentry",
        keywords: "position assets liabilities", load: () => import("../modules/accounts/BalanceSheet"),
      },
      {
        path: "gst", label: "GST returns", permission: "gst.compile_returns",
        keywords: "gstr1 gstr3b tax return", load: () => import("../modules/accounts/GstReturns"),
      },
    ],
  },
  {
    key: "payroll",
    label: "People",
    icon: "people",
    screens: [
      {
        path: "leave", label: "Leave", permission: "hr.view_leaverequest", create: "hr.add_leaverequest",
        keywords: "holiday absence time off vacation sick", load: () => import("../modules/payroll/LeaveList"),
        detail: () => import("../modules/payroll/LeaveForm"),
      },
      {
        path: "runs", label: "Pay runs", permission: "hr.view_payrun", create: "hr.add_payrun",
        keywords: "salary wages payslip", load: () => import("../modules/payroll/PayRuns"),
        detail: () => import("../modules/payroll/PayRunForm"),
      },
      {
        path: "dues", label: "Statutory dues", permission: "hr.view_statutoryremittance",
        keywords: "pf esi tds professional tax remittance", load: () => import("../modules/payroll/Liabilities"),
      },
    ],
  },
  {
    key: "stores",
    label: "Stores",
    icon: "stock",
    screens: [
      {
        path: "on-hand", label: "Stock on hand", permission: "inventory.view_stockmovement",
        keywords: "inventory stock valuation balance", load: () => import("../modules/stores/StockOnHand"),
      },
      {
        path: "movements", label: "Movements", permission: "inventory.view_stockmovement",
        keywords: "stock ledger in out", load: () => import("../modules/stores/Movements"),
      },
      {
        path: "items", label: "Items", permission: "inventory.view_item",
        keywords: "product sku material", load: () => import("../modules/stores/Items"),
      },
      {
        path: "counts", label: "Stock counts", permission: "inventory.view_stockcount", create: "inventory.add_stockcount",
        keywords: "physical count stocktake verification", load: () => import("../modules/stores/Counts"),
        detail: () => import("../modules/stores/CountForm"),
      },
      {
        path: "transfers", label: "Transfers", permission: "inventory.view_stocktransfer", create: "inventory.add_stocktransfer",
        keywords: "move warehouse transit lorry branch", load: () => import("../modules/stores/Transfers"),
        detail: () => import("../modules/stores/TransferForm"),
      },
      {
        path: "adjustments", label: "Adjustments", permission: "inventory.view_stockadjustment", create: "inventory.add_stockadjustment",
        keywords: "write off damage scrap samples opening", load: () => import("../modules/stores/Adjustments"),
        detail: () => import("../modules/stores/AdjustmentForm"),
      },
      {
        path: "batches", label: "Batches", permission: "inventory.view_lot",
        keywords: "lot batch granule roll expiry", load: () => import("../modules/stores/Lots"),
        detail: () => import("../modules/stores/LotForm"),
      },
      {
        path: "reserved", label: "Reserved stock", permission: "inventory.view_stockreservation",
        keywords: "reservation allocated held promised", load: () => import("../modules/stores/Reservations"),
      },
      {
        path: "value", label: "Stock value", permission: "inventory.view_stockmovement",
        keywords: "valuation inventory value report", load: () => import("../modules/stores/Valuation"),
      },
      {
        path: "against-books", label: "Stock against the books", permission: "inventory.view_stockmovement",
        keywords: "reconciliation ledger inventory account", load: () => import("../modules/stores/Reconciliation"),
      },
      {
        path: "slow-moving", label: "Slow-moving stock", permission: "inventory.view_stockmovement",
        keywords: "dead stock non moving aging", load: () => import("../modules/stores/SlowMoving"),
      },
      {
        path: "negative", label: "Negative stock", permission: "inventory.view_stockmovement",
        keywords: "below zero errors", load: () => import("../modules/stores/Negative"),
      },
      {
        path: "bins", label: "Bins", permission: "inventory.view_storagebin", create: "inventory.add_storagebin",
        keywords: "location rack shelf", load: () => import("../modules/stores/Bins"),
        detail: () => import("../modules/stores/BinForm"),
      },
      {
        path: "reasons", label: "Adjustment reasons", permission: "inventory.view_adjustmentreason",
        create: "inventory.add_adjustmentreason",
        keywords: "write off reason account", load: () => import("../modules/stores/Reasons"),
        detail: () => import("../modules/stores/ReasonForm"),
      },
    ],
  },
];

export function screenUrl(module: Module, screen: Screen): string {
  return `/${module.key}/${screen.path}`;
}
