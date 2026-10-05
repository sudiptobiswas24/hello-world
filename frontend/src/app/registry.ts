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
}

export interface Module {
  key: string;
  label: string;
  icon: IconName;
  screens: Screen[];
}

export type IconName = "home" | "sales" | "purchasing" | "stock" | "production" | "accounts";

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
];

export function screenUrl(module: Module, screen: Screen): string {
  return `/${module.key}/${screen.path}`;
}
