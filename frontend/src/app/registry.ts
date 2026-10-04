import type { ComponentType } from "react";

/**
 * Every screen the application has, in one list: the navigation, the
 * routes and the command palette all read it, so a screen cannot be
 * reachable from one and missing from another.
 *
 * `permission` is the one the server checks to read what the screen
 * shows; a screen is offered to whoever holds it.
 */
export interface Screen {
  path: string; // under the module, e.g. "invoices"
  label: string;
  permission: string;
  keywords?: string;
  load: () => Promise<{ default: ComponentType }>;
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
        path: "invoices",
        label: "Invoices",
        permission: "sales.view_invoice",
        keywords: "bill credit note customer",
        load: () => import("../modules/sales/Invoices"),
      },
      {
        path: "orders",
        label: "Sales orders",
        permission: "sales.view_salesorder",
        keywords: "so order customer",
        load: () => import("../modules/sales/Orders"),
      },
      {
        path: "customers",
        label: "Customers",
        permission: "core.view_party",
        keywords: "party buyer",
        load: () => import("../modules/sales/Customers"),
      },
    ],
  },
];

export function screenUrl(module: Module, screen: Screen): string {
  return `/${module.key}/${screen.path}`;
}
