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
  /**
   * A master every role reads (currencies, units) is offered in the
   * navigation only to whoever keeps it; the rest reach a record of it
   * from where it is used.
   */
  keep?: string;
}

/** Whether to offer a screen: what it reads first, and everything else it needs. */
export function offered(screen: Screen, can: (permission: string) => boolean): boolean {
  return !screen.hidden && can(screen.permission) && (screen.also ?? []).every(can)
    && (!screen.keep || can(screen.keep));
}

export interface Module {
  key: string;
  label: string;
  icon: IconName;
  screens: Screen[];
}

export type IconName = "home" | "sales" | "purchasing" | "stock" | "production" | "making" | "plant" | "quality" | "accounts" | "people" | "settings";

export const MODULES: Module[] = [
  {
    key: "sales",
    label: "Sales",
    icon: "sales",
    screens: [
      {
        path: "unacknowledged", label: "Not yet signed for", permission: "sales.view_delivery",
        keywords: "proof of delivery pod received grn acknowledgement", load: () => import("../modules/sales/Unacknowledged"),
      },
      {
        path: "orders", label: "Orders", permission: "sales.view_salesorder", create: "sales.add_salesorder",
        keywords: "so sales order customer", load: () => import("../modules/sales/Orders"),
        detail: () => import("../modules/sales/OrderForm"),
      },
      {
        path: "leads", label: "Leads", permission: "sales.view_lead", create: "sales.add_lead",
        keywords: "lead enquiry prospect crm", load: () => import("../modules/sales/Leads"),
        detail: () => import("../modules/sales/LeadForm"),
      },
      {
        path: "opportunities", label: "Opportunities", permission: "sales.view_opportunity", create: "sales.add_opportunity",
        keywords: "opportunity pipeline deal crm stage", load: () => import("../modules/sales/Opportunities"),
        detail: () => import("../modules/sales/OpportunityForm"),
      },
      {
        path: "pipeline", label: "Pipeline", permission: "sales.view_opportunity",
        keywords: "pipeline stages weighted value crm forecast", load: () => import("../modules/sales/Pipeline"),
      },
      {
        path: "activities", label: "Calls and visits", permission: "sales.view_activity", create: "sales.add_activity",
        keywords: "activity call visit follow-up crm note", load: () => import("../modules/sales/Activities"),
        detail: () => import("../modules/sales/ActivityForm"),
      },
      {
        path: "campaigns", label: "Campaigns", permission: "sales.view_campaign", create: "sales.add_campaign",
        keywords: "campaign exhibition marketing crm", load: () => import("../modules/sales/Campaigns"),
        detail: () => import("../modules/sales/CampaignForm"),
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
        path: "releases", label: "Third-party releases", permission: "sales.view_thirdpartyrelease",
        keywords: "sgs bureau veritas agency inspection release", load: () => import("../modules/sales/Releases"),
        detail: () => import("../modules/sales/ReleaseForm"),
      },
      {
        path: "invoices", label: "Invoices", permission: "sales.view_invoice", create: "sales.add_invoice",
        keywords: "bill credit note customer", load: () => import("../modules/sales/Invoices"),
        detail: () => import("../modules/sales/InvoiceForm"),
      },
      {
        path: "claims", label: "Claims", permission: "sales.view_invoice",
        keywords: "claim torn short weight rate dispute deduction quality cost", load: () => import("../modules/sales/Claims"),
        detail: () => import("../modules/sales/ClaimForm"),
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
      {
        path: "call-offs", label: "Call-offs", permission: "sales.view_calloff",
        create: "sales.add_calloff",
        keywords: "schedule release delivery call off", load: () => import("../modules/sales/CallOffs"),
        detail: () => import("../modules/sales/CallOffForm"),
      },
      {
        path: "price-lists", label: "Price lists", permission: "sales.view_pricelist",
        create: "sales.add_pricelist",
        keywords: "price list rate quantity break", load: () => import("../modules/sales/PriceLists"),
        detail: () => import("../modules/sales/PriceListForm"),
      },
      {
        path: "price-indices", label: "Price indices", permission: "sales.view_priceindex",
        create: "sales.add_priceindex",
        keywords: "polymer index pp price variation", load: () => import("../modules/sales/PriceIndices"),
        detail: () => import("../modules/sales/PriceIndexForm"),
      },
      {
        path: "price-clauses", label: "Price variation clauses", permission: "sales.view_pricevariationclause",
        create: "sales.add_pricevariationclause",
        keywords: "pvc polymer escalation clause", load: () => import("../modules/sales/PriceClauses"),
        detail: () => import("../modules/sales/PriceClauseForm"),
      },
      {
        path: "variation-bills", label: "Price variation bills", permission: "sales.view_pricevariationbill",
        also: ["sales.view_salesorder"],
        keywords: "pvc escalation debit credit polymer", load: () => import("../modules/sales/VariationBills"),
      },
      {
        path: "supplied-items", label: "Material the customer sends", permission: "sales.view_supplieditem",
        create: "sales.add_supplieditem",
        keywords: "job work customer supplied material", load: () => import("../modules/sales/SuppliedItems"),
        detail: () => import("../modules/sales/SuppliedItemForm"),
      },
      {
        path: "recurring", label: "Recurring invoices", permission: "sales.view_recurringinvoice",
        create: "sales.add_recurringinvoice",
        keywords: "rent contract subscription repeat", load: () => import("../modules/sales/RecurringInvoices"),
        detail: () => import("../modules/sales/RecurringInvoiceForm"),
      },
      {
        path: "reminders", label: "Payment reminders", permission: "sales.view_dunningnotice",
        keywords: "dunning overdue reminder statement chase", load: () => import("../modules/sales/Reminders"),
      },
      {
        path: "reminder-levels", label: "Reminder levels", permission: "sales.view_dunninglevel",
        create: "sales.add_dunninglevel",
        keywords: "dunning level template email", load: () => import("../modules/sales/DunningLevels"),
        detail: () => import("../modules/sales/DunningLevelForm"),
      },
      {
        path: "reps", label: "Sales reps", permission: "sales.view_salesrep",
        create: "sales.add_salesrep",
        keywords: "salesman representative commission", load: () => import("../modules/sales/SalesReps"),
        detail: () => import("../modules/sales/SalesRepForm"),
      },
      {
        path: "commission-plans", label: "Commission plans", permission: "sales.view_commissionplan",
        create: "sales.add_commissionplan",
        keywords: "commission percent basis", load: () => import("../modules/sales/CommissionPlans"),
        detail: () => import("../modules/sales/CommissionPlanForm"),
      },
      {
        path: "commission", label: "Commission", permission: "sales.view_commissionplan",
        keywords: "commission earned rep", load: () => import("../modules/sales/Commission"),
      },
      {
        path: "bad-debts", label: "Bad debts", permission: "sales.view_invoice",
        keywords: "write off bad debt recovered", load: () => import("../modules/sales/BadDebt"),
      },
    ],
  },
  {
    key: "purchasing",
    label: "Purchasing",
    icon: "purchasing",
    screens: [
      {
        path: "unbilled-freight", label: "Freight not yet billed", permission: "purchasing.view_bill",
        also: ["sales.view_delivery", "purchasing.view_purchaseorder"],
        keywords: "freight transporter lr lorry receipt unbilled", load: () => import("../modules/purchasing/UnbilledFreight"),
      },
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
        path: "receive-by-scan", label: "Receive by scan", permission: "purchasing.add_goodsreceipt",
        also: ["purchasing.view_purchaseorder", "inventory.view_warehouse"],
        keywords: "scan barcode gate receive purchase order", load: () => import("../modules/purchasing/ReceiveByScan"),
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
      {
        path: "requisitions", label: "Requisitions", permission: "purchasing.view_purchaserequisition", create: "purchasing.add_purchaserequisition",
        also: ["purchasing.view_purchaserequisitionline"],
        keywords: "request indent ask approve", load: () => import("../modules/purchasing/Requisitions"),
        detail: () => import("../modules/purchasing/RequisitionForm"),
      },
      {
        path: "rfqs", label: "Requests for quotation", permission: "purchasing.view_requestforquotation", create: "purchasing.add_requestforquotation",
        also: ["purchasing.view_rfqline", "purchasing.view_rfqinvitation"],
        keywords: "rfq quotation tender compare quotes award", load: () => import("../modules/purchasing/Rfqs"),
        detail: () => import("../modules/purchasing/RfqForm"),
      },
      {
        path: "blanket-orders", label: "Blanket orders", permission: "purchasing.view_blanketorder", create: "purchasing.add_blanketorder",
        also: ["purchasing.view_blanketorderline"],
        keywords: "blanket contract agreement release call off", load: () => import("../modules/purchasing/BlanketOrders"),
        detail: () => import("../modules/purchasing/BlanketOrderForm"),
      },
      {
        path: "vendor-prices", label: "Vendor prices", permission: "purchasing.view_vendorprice", create: "purchasing.add_vendorprice",
        keywords: "agreed price break lead time preferred vendor", load: () => import("../modules/purchasing/VendorPrices"),
        detail: () => import("../modules/purchasing/VendorPriceForm"),
      },
      {
        path: "reorder", label: "What to reorder", permission: "purchasing.view_purchaseorder",
        keywords: "reorder suggestions under minimum level shortfall order", load: () => import("../modules/purchasing/Reorder"),
      },
      {
        path: "reorder-rules", label: "Reorder rules", permission: "purchasing.view_reorderrule", create: "purchasing.add_reorderrule",
        keywords: "minimum maximum reorder level min max", load: () => import("../modules/purchasing/ReorderRules"),
        detail: () => import("../modules/purchasing/ReorderRuleForm"),
      },
      {
        path: "budgets", label: "Budgets", permission: "purchasing.view_budget", create: "purchasing.add_budget",
        keywords: "budget spend committed available", load: () => import("../modules/purchasing/Budgets"),
        detail: () => import("../modules/purchasing/BudgetForm"),
      },
      {
        path: "approval-policies", label: "Purchase approval", permission: "purchasing.view_purchaseapprovalpolicy", create: "purchasing.add_purchaseapprovalpolicy",
        keywords: "approval limit threshold tier sign off", load: () => import("../modules/purchasing/ApprovalPolicies"),
        detail: () => import("../modules/purchasing/ApprovalPolicyForm"),
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
        path: "bales", label: "Bales", permission: "manufacturing.view_bale",
        keywords: "bale packed bundle dispatch", load: () => import("../modules/floor/Bales"),
        detail: () => import("../modules/floor/BaleForm"),
      },
      {
        path: "rolls", label: "Fabric rolls", permission: "manufacturing.view_fabricroll",
        keywords: "roll loom gsm metres weight", load: () => import("../modules/floor/Rolls"),
      },
      {
        path: "rebatches", label: "Rebatches", permission: "manufacturing.view_rebatch",
        keywords: "split join merge batch lot", load: () => import("../modules/floor/Rebatches"),
        detail: () => import("../modules/floor/RebatchForm"),
      },
      {
        path: "operator-yield", label: "Operator yield", permission: "manufacturing.view_timebooking",
        also: ["manufacturing.view_workcentre"],
        keywords: "operator performance people productivity", load: () => import("../modules/floor/OperatorYield"),
      },
      {
        path: "scrap", label: "Scrap by reason", permission: "manufacturing.view_workorder",
        keywords: "scrap waste reason step", load: () => import("../modules/floor/ScrapReport"),
      },
      {
        path: "promise", label: "When can we promise?", permission: "planning.view_plannedorder",
        also: ["inventory.view_warehouse"],
        keywords: "atp ctp delivery date available to promise", load: () => import("../modules/production/WhenCanWePromise"),
      },
      {
        path: "shipment-history", label: "Shipment history", permission: "planning.view_shipmenthistory",
        create: "planning.add_shipmenthistory",
        keywords: "shipment history old system months forecast import", load: () => import("../modules/planning/ShipmentHistory"),
        detail: () => import("../modules/planning/ShipmentHistoryForm"),
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
        path: "planning-settings", label: "Planning settings", permission: "planning.view_planningsettings",
        keep: "planning.change_planningsettings",
        keywords: "horizon fence lead time requisition", load: () => import("../modules/production/PlanningSettingsList"),
        detail: () => import("../modules/production/PlanningSettingsForm"),
      },
      {
        path: "plan-actions", label: "What the plan says to do", permission: "planning.view_planningaction",
        keywords: "expedite defer cancel reschedule exception", load: () => import("../modules/production/PlanningActions"),
      },
      {
        path: "planned-for", label: "What planned orders are for", permission: "planning.view_planneddemand",
        keywords: "pegging demand where used", load: () => import("../modules/production/PlannedDemands"),
      },
      {
        path: "levels", label: "Explosion levels", permission: "planning.view_plannedorder",
        keywords: "low level code bom level cycle", load: () => import("../modules/production/Levels"),
      },
      {
        path: "step-counts", label: "Step counts", permission: "manufacturing.view_operationreport",
        create: "manufacturing.add_operationreport",
        keywords: "operation report step output count", load: () => import("../modules/floor/StepCounts"),
        detail: () => import("../modules/floor/StepCountForm"),
      },
      {
        path: "station-report", label: "Station morning report", permission: "manufacturing.view_loomstation",
        keywords: "8 am morning loom rolls exceptions", load: () => import("../modules/floor/StationReport"),
      },
      {
        path: "profitability", label: "Order profitability", permission: "manufacturing.view_costsheet",
        also: ["sales.view_salesorder", "sales.view_salesorderline"],
        keywords: "margin quote actual cost order", load: () => import("../modules/floor/Profitability"),
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
        path: "manufacturing-settings", label: "Manufacturing settings", permission: "manufacturing.view_manufacturingsettings",
        keep: "manufacturing.change_manufacturingsettings",
        keywords: "wip work in progress variance scrap revaluation accounts", load: () => import("../modules/making/ManufacturingSettingsList"),
        detail: () => import("../modules/making/ManufacturingSettingsForm"),
      },
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
      {
        path: "cost-sheets", label: "Cost sheets", permission: "manufacturing.view_costsheet",
        create: "manufacturing.add_costsheet",
        keywords: "costing quotation price sack cost", load: () => import("../modules/making/CostSheets"),
        detail: () => import("../modules/making/CostSheetForm"),
      },
      {
        path: "bags", label: "Bag specifications", permission: "manufacturing.view_bagspecification",
        create: "manufacturing.add_bagspecification",
        keywords: "sack bag size print lamination specification", load: () => import("../modules/making/BagSpecs"),
        detail: () => import("../modules/making/BagSpecForm"),
      },
      {
        path: "fabrics", label: "Fabric specifications", permission: "manufacturing.view_fabricspecification",
        create: "manufacturing.add_fabricspecification",
        keywords: "fabric mesh gsm weave specification", load: () => import("../modules/making/FabricSpecs"),
        detail: () => import("../modules/making/FabricSpecForm"),
      },
      {
        path: "tapes", label: "Tape specifications", permission: "manufacturing.view_tapespecification",
        create: "manufacturing.add_tapespecification",
        keywords: "tape denier blend filler specification", load: () => import("../modules/making/TapeSpecs"),
        detail: () => import("../modules/making/TapeSpecForm"),
      },
      {
        path: "films", label: "Film specifications", permission: "manufacturing.view_filmspecification",
        create: "manufacturing.add_filmspecification",
        keywords: "film liner micron blown", load: () => import("../modules/making/FilmSpecs"),
        detail: () => import("../modules/making/FilmSpecForm"),
      },
      {
        path: "liners", label: "Liner specifications", permission: "manufacturing.view_linerspecification",
        create: "manufacturing.add_linerspecification",
        keywords: "liner cut seal", load: () => import("../modules/making/LinerSpecs"),
        detail: () => import("../modules/making/LinerSpecForm"),
      },
      {
        path: "designs", label: "Print designs", permission: "manufacturing.view_printdesign",
        create: "manufacturing.add_printdesign",
        keywords: "artwork print design cylinder colours", load: () => import("../modules/making/PrintDesigns"),
        detail: () => import("../modules/making/PrintDesignForm"),
      },
      {
        path: "material-rates", label: "Material rates", permission: "manufacturing.view_materialrate",
        create: "manufacturing.add_materialrate",
        keywords: "polymer price rate sheet quotation", load: () => import("../modules/making/MaterialRates"),
        detail: () => import("../modules/making/MaterialRateForm"),
      },
      {
        path: "stage-rates", label: "Conversion rates", permission: "manufacturing.view_stagerate",
        create: "manufacturing.add_stagerate",
        keywords: "conversion cost rate stage quotation", load: () => import("../modules/making/StageRates"),
        detail: () => import("../modules/making/StageRateForm"),
      },
      {
        path: "quote-policies", label: "Quotation policy", permission: "manufacturing.view_quotepolicy",
        create: "manufacturing.add_quotepolicy",
        keywords: "overhead margin quotation", load: () => import("../modules/making/QuotePolicies"),
        detail: () => import("../modules/making/QuotePolicyForm"),
      },
      {
        path: "cost-versions", label: "Standard costs", permission: "manufacturing.view_costversion",
        create: "manufacturing.add_costversion",
        also: ["manufacturing.view_standardcost"],
        keywords: "standard cost version roll up revalue", load: () => import("../modules/making/CostVersions"),
        detail: () => import("../modules/making/CostVersionForm"),
      },
    ],
  },
  {
    key: "plant",
    label: "Plant",
    icon: "plant",
    screens: [
      {
        path: "daily", label: "Yesterday's production", permission: "manufacturing.view_productionentry",
        also: ["manufacturing.view_workcentre"],
        keywords: "daily production report tonnes wastage kwh per kg morning yesterday section",
        load: () => import("../modules/plant/DailyProduction"),
      },
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
        path: "critical-spares", label: "Critical spares", permission: "manufacturing.view_machineposition",
        also: ["inventory.view_item"],
        keywords: "critical spares positions bearing life keep in stock", load: () => import("../modules/plant/CriticalSpares"),
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
        path: "certificates", label: "Test certificates", permission: "manufacturing.view_testcertificate",
        create: "manufacturing.add_testcertificate", also: ["sales.view_delivery"],
        keywords: "test certificate coa shipment", load: () => import("../modules/quality/Certificates"),
        detail: () => import("../modules/quality/CertificateForm"),
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
      {
        path: "quality-settings", label: "Quality settings", permission: "quality.view_qualitysettings",
        keep: "quality.change_qualitysettings",
        keywords: "concession second person segregation", load: () => import("../modules/quality/QualitySettingsList"),
        detail: () => import("../modules/quality/QualitySettingsForm"),
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
      {
        path: "assets", label: "Fixed assets", permission: "assets.view_fixedasset",
        create: "assets.add_fixedasset",
        keywords: "machine building vehicle depreciation capital", load: () => import("../modules/accounts/Assets"),
        detail: () => import("../modules/accounts/AssetForm"),
      },
      {
        path: "asset-register", label: "Fixed asset register", permission: "assets.view_fixedasset",
        keywords: "book value depreciation register", load: () => import("../modules/accounts/AssetRegister"),
      },
      {
        path: "bank-statements", label: "Bank statements", permission: "accounting.view_bankstatement",
        create: "accounting.add_bankstatement",
        keywords: "reconciliation brs bank reconcile cheque unpresented", load: () => import("../modules/accounts/BankStatements"),
        detail: () => import("../modules/accounts/BankStatementForm"),
      },
      {
        path: "tds-deducted", label: "TDS deducted", permission: "purchasing.view_tdsdeduction",
        keywords: "tds 194q 194c 194j deducted vendor withholding", load: () => import("../modules/accounts/TdsDeductions"),
        detail: () => import("../modules/accounts/TdsDeductionForm"),
      },
      {
        path: "tds-challans", label: "TDS challans", permission: "purchasing.view_tdschallan",
        create: "purchasing.add_tdschallan",
        keywords: "tds challan 281 pay over bsr", load: () => import("../modules/accounts/TdsChallans"),
        detail: () => import("../modules/accounts/TdsChallanForm"),
      },
      {
        path: "tds-return", label: "TDS return", permission: "purchasing.view_tdsdeduction",
        keywords: "26q quarterly tds return", load: () => import("../modules/accounts/TdsReturn"),
      },
      {
        path: "bank-stock-statement", label: "Bank stock statement", permission: "accounting.view_journalentry",
        keywords: "bank stock statement drawing power cash credit debtors creditors margin",
        load: () => import("../modules/accounts/BankStockStatement"),
      },
      {
        path: "msme-payments", label: "MSME payments", permission: "purchasing.view_bill",
        also: ["purchasing.view_purchaseorder"],
        keywords: "msme 43b udyam 45 days micro small late payment", load: () => import("../modules/accounts/MsmeReport"),
      },
      {
        path: "customer-tds", label: "TDS by customers", permission: "sales.view_customertds",
        create: "sales.add_customertds",
        keywords: "tds receivable 26as form 16a customer deducted", load: () => import("../modules/accounts/CustomerTdsList"),
        detail: () => import("../modules/accounts/CustomerTdsForm"),
      },
      {
        path: "bad-debts", label: "Bad debts", permission: "sales.view_invoicewriteoff",
        keywords: "write off bad debt recover uncollectable", load: () => import("../modules/accounts/WriteOffs"),
        detail: () => import("../modules/accounts/WriteOffForm"),
      },
      {
        path: "asset-categories", label: "Asset categories", permission: "assets.view_assetcategory",
        create: "assets.add_assetcategory",
        keywords: "depreciation method life", load: () => import("../modules/accounts/AssetCategories"),
        detail: () => import("../modules/accounts/AssetCategoryForm"),
      },
      {
        path: "e-invoices", label: "E-invoices", permission: "gst.view_einvoice",
        create: "gst.add_einvoice",
        keywords: "irn einvoice gst portal", load: () => import("../modules/gst/EInvoices"),
        detail: () => import("../modules/gst/EInvoiceForm"),
      },
      {
        path: "eway-bills", label: "E-way bills", permission: "gst.view_ewaybill",
        create: "gst.add_ewaybill",
        keywords: "eway e-way transport vehicle lr", load: () => import("../modules/gst/EwayBills"),
        detail: () => import("../modules/gst/EwayBillForm"),
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
      {
        path: "employees", label: "Employees", permission: "hr.view_employee",
        create: "hr.add_employee",
        keywords: "staff worker operator person hire", load: () => import("../modules/payroll/Employees"),
        detail: () => import("../modules/payroll/EmployeeForm"),
      },
      {
        path: "attendance", label: "Attendance", permission: "hr.view_attendanceday", create: "hr.add_attendanceday",
        keywords: "attendance register shift present absent half day late overtime",
        load: () => import("../modules/payroll/Attendance"),
        detail: () => import("../modules/payroll/AttendanceForm"),
      },
      {
        path: "punch-file", label: "Punch file", permission: "hr.add_attendanceday",
        keywords: "biometric reader punch csv import attendance", load: () => import("../modules/payroll/PunchFile"),
      },
      {
        path: "attendance-gaps", label: "Not yet marked", permission: "hr.view_attendanceday",
        keywords: "attendance unmarked day rated pay run gaps", load: () => import("../modules/payroll/AttendanceGaps"),
      },
      {
        path: "departments", label: "Departments", permission: "hr.view_department",
        create: "hr.add_department",
        keywords: "department cost centre", load: () => import("../modules/payroll/Departments"),
        detail: () => import("../modules/payroll/DepartmentForm"),
      },
      {
        path: "pay-components", label: "Pay components", permission: "hr.view_paycomponent",
        create: "hr.add_paycomponent",
        keywords: "earning deduction pf esi professional tax slab", load: () => import("../modules/payroll/PayComponents"),
        detail: () => import("../modules/payroll/PayComponentForm"),
      },
      {
        path: "leave-policies", label: "Leave policies", permission: "hr.view_leavepolicy",
        keywords: "leave entitlement days", load: () => import("../modules/payroll/LeavePolicies"),
        detail: () => import("../modules/payroll/LeavePolicyForm"),
      },
      {
        path: "gratuity", label: "Gratuity", permission: "hr.view_employeecompensation",
        keywords: "gratuity provision years of service", load: () => import("../modules/payroll/Gratuity"),
      },
      {
        path: "labour-contractors", label: "Labour contractors", permission: "hr.view_labourcontractor",
        create: "hr.add_labourcontractor",
        keywords: "clra contract labour licence form xii xiii", load: () => import("../modules/payroll/LabourContractors"),
        detail: () => import("../modules/payroll/LabourContractorForm"),
      },
      {
        path: "remittances", label: "Statutory remittances", permission: "hr.view_statutoryremittance",
        create: "hr.add_statutoryremittance",
        keywords: "pf esi tds paid challan", load: () => import("../modules/payroll/Remittances"),
        detail: () => import("../modules/payroll/RemittanceForm"),
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
        path: "items", label: "Items", permission: "inventory.view_item", create: "inventory.add_item",
        keywords: "product sku material", load: () => import("../modules/stores/Items"),
        detail: () => import("../modules/stores/ItemForm"),
      },
      {
        path: "warehouses", label: "Warehouses", permission: "inventory.view_warehouse",
        keep: "inventory.change_warehouse",
        create: "inventory.add_warehouse",
        keywords: "store godown location quarantine", load: () => import("../modules/stores/Warehouses"),
        detail: () => import("../modules/stores/WarehouseForm"),
      },
      {
        path: "item-templates", label: "Item templates", permission: "inventory.view_itemtemplate",
        create: "inventory.add_itemtemplate",
        keywords: "variant size colour template", load: () => import("../modules/stores/ItemTemplates"),
        detail: () => import("../modules/stores/ItemTemplateForm"),
      },
      {
        path: "item-attributes", label: "Item attributes", permission: "inventory.view_itemattribute",
        create: "inventory.add_itemattribute",
        keywords: "variant attribute size colour", load: () => import("../modules/stores/ItemAttributes"),
        detail: () => import("../modules/stores/ItemAttributeForm"),
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
      {
        path: "job-work", label: "Job-work challans", permission: "manufacturing.view_jobworkchallan",
        create: "manufacturing.add_jobworkchallan",
        keywords: "job worker challan itc-04 outside", load: () => import("../modules/stores/JobWork"),
        detail: () => import("../modules/stores/JobWorkForm"),
      },
      {
        path: "customer-material", label: "Customer material in", permission: "manufacturing.view_customermaterialreceipt",
        create: "manufacturing.add_customermaterialreceipt",
        keywords: "job work inward customer supplied", load: () => import("../modules/stores/CustomerReceipts"),
        detail: () => import("../modules/stores/CustomerReceiptForm"),
      },
      {
        path: "customer-material-back", label: "Customer material back", permission: "manufacturing.view_customermaterialreturn",
        create: "manufacturing.add_customermaterialreturn",
        keywords: "return customer supplied material", load: () => import("../modules/stores/CustomerReturns"),
        detail: () => import("../modules/stores/CustomerReturnForm"),
      },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    icon: "settings",
    screens: [
      {
        path: "company", label: "Company", permission: "core.view_company",
        keep: "core.change_company",
        keywords: "base currency default accounts fiscal year gstin", load: () => import("../modules/settings/CompanyList"),
        detail: () => import("../modules/settings/CompanyForm"),
      },
      {
        path: "currencies", label: "Currencies", permission: "core.view_currency",
        keep: "core.change_currency",
        create: "core.add_currency",
        keywords: "currency base inr usd", load: () => import("../modules/settings/Currencies"),
        detail: () => import("../modules/settings/CurrencyForm"),
      },
      {
        path: "exchange-rates", label: "Exchange rates", permission: "core.view_exchangerate",
        keep: "core.change_exchangerate",
        create: "core.add_exchangerate",
        keywords: "fx rate forex", load: () => import("../modules/settings/ExchangeRates"),
        detail: () => import("../modules/settings/ExchangeRateForm"),
      },
      {
        path: "countries", label: "Countries", permission: "core.view_country",
        keep: "core.change_country",
        create: "core.add_country",
        keywords: "country", load: () => import("../modules/settings/Countries"),
        detail: () => import("../modules/settings/CountryForm"),
      },
      {
        path: "units", label: "Units of measure", permission: "core.view_unitofmeasure",
        keep: "core.change_unitofmeasure",
        create: "core.add_unitofmeasure",
        keywords: "uom unit kg pcs conversion", load: () => import("../modules/settings/Units"),
        detail: () => import("../modules/settings/UnitForm"),
      },
      {
        path: "charge-types", label: "Charge types", permission: "accounting.view_chargetype",
        keep: "accounting.change_chargetype",
        create: "accounting.add_chargetype",
        keywords: "freight loading surcharge sac", load: () => import("../modules/settings/ChargeTypes"),
        detail: () => import("../modules/settings/ChargeTypeForm"),
      },
      {
        path: "licences", label: "Licences", permission: "core.view_licence", keep: "core.change_licence",
        create: "core.add_licence",
        keywords: "licence factory consent pollution fire noc stamping metrology boiler renewal calendar lapse",
        load: () => import("../modules/settings/Licences"),
        detail: () => import("../modules/settings/LicenceForm"),
      },
      {
        path: "tds-sections", label: "TDS sections", permission: "accounting.view_tdssection",
        keep: "accounting.change_tdssection",
        create: "accounting.add_tdssection",
        keywords: "tds 194q 194c 194j rate threshold", load: () => import("../modules/settings/TdsSections"),
        detail: () => import("../modules/settings/TdsSectionForm"),
      },
      {
        path: "numbering", label: "Document numbering", permission: "core.view_documentsequence",
        keep: "core.change_documentsequence",
        create: "core.add_documentsequence",
        keywords: "sequence prefix invoice number series", load: () => import("../modules/settings/DocumentSequences"),
        detail: () => import("../modules/settings/DocumentSequenceForm"),
      },
      {
        path: "payment-terms", label: "Payment terms", permission: "core.view_paymentterms",
        keep: "core.change_paymentterms",
        create: "core.add_paymentterms",
        keywords: "credit days net discount terms", load: () => import("../modules/settings/PaymentTermsList"),
        detail: () => import("../modules/settings/PaymentTermsForm"),
      },
      {
        path: "party-tags", label: "Party tags", permission: "core.view_partytag",
        keep: "core.change_partytag",
        create: "core.add_partytag",
        keywords: "tag group segment", load: () => import("../modules/settings/PartyTags"),
        detail: () => import("../modules/settings/PartyTagForm"),
      },
      {
        path: "taxes", label: "Taxes", permission: "accounting.view_tax",
        keep: "accounting.change_tax",
        create: "accounting.add_tax",
        keywords: "gst cgst sgst igst cess rate", load: () => import("../modules/settings/Taxes"),
        detail: () => import("../modules/settings/TaxForm"),
      },
      {
        path: "tax-groups", label: "Tax groups", permission: "accounting.view_taxgroup",
        keep: "accounting.change_taxgroup",
        create: "accounting.add_taxgroup",
        keywords: "tax group", load: () => import("../modules/settings/TaxGroups"),
        detail: () => import("../modules/settings/TaxGroupForm"),
      },
      {
        path: "fiscal-positions", label: "Fiscal positions", permission: "accounting.view_fiscalposition",
        keep: "accounting.change_fiscalposition",
        create: "accounting.add_fiscalposition",
        keywords: "export sez tax mapping", load: () => import("../modules/settings/FiscalPositions"),
        detail: () => import("../modules/settings/FiscalPositionForm"),
      },
      {
        path: "gst-registrations", label: "GST registrations", permission: "accounting.view_partytaxprofile",
        keep: "accounting.change_partytaxprofile",
        create: "accounting.add_partytaxprofile",
        keywords: "gstin registered composition sez unregistered exempt", load: () => import("../modules/settings/GstRegistrations"),
        detail: () => import("../modules/settings/GstRegistrationForm"),
      },
    ],
  },
];

export function screenUrl(module: Module, screen: Screen): string {
  return `/${module.key}/${screen.path}`;
}
