import type { SmartDef } from "../../views/SmartButtons";

/** What has passed between us and a customer, each opening its list. */
export function customerButtons(id: number): SmartDef[] {
  const customer = String(id);
  return [
    { label: "Orders", endpoint: "/api/sales/sales-orders/", query: { customer }, permission: "sales.view_salesorder", screen: "/sales/orders" },
    { label: "To ship", endpoint: "/api/sales/sales-orders/", query: { customer, status: "confirmed", to_ship: "true" },
      permission: "sales.view_salesorder", screen: "/sales/orders" },
    { label: "Quotations", endpoint: "/api/sales/quotations/", query: { customer }, permission: "sales.view_quotation", screen: "/sales/quotations" },
    { label: "Deliveries", endpoint: "/api/sales/deliveries/", query: { sales_order__customer: customer },
      permission: "sales.view_delivery", screen: "/sales/deliveries" },
    { label: "Not yet paid", endpoint: "/api/sales/invoices/", query: { customer, open: "true" },
      permission: "sales.view_invoice", screen: "/sales/invoices", urgent: true },
    { label: "Money received", endpoint: "/api/accounting/payments/", query: { party: customer, direction: "receipt" },
      permission: "accounting.view_payment", screen: "/sales/receipts" },
    { label: "Complaints", endpoint: "/api/manufacturing/complaints/", query: { customer },
      permission: "manufacturing.view_complaint", screen: "/quality/complaints" },
    { label: "Opportunities", endpoint: "/api/sales/opportunities/", query: { customer },
      permission: "sales.view_opportunity", screen: "/sales/opportunities" },
  ];
}

/** What an order has led to. */
export function orderButtons(id: number): SmartDef[] {
  const order = String(id);
  return [
    { label: "Deliveries", endpoint: "/api/sales/deliveries/", query: { sales_order: order }, permission: "sales.view_delivery", screen: "/sales/deliveries" },
    { label: "Invoices", endpoint: "/api/sales/invoices/", query: { sales_order: order }, permission: "sales.view_invoice", screen: "/sales/invoices" },
    { label: "Runs", endpoint: "/api/manufacturing/work-orders/", query: { sales_order_line__order: order },
      permission: "manufacturing.view_workorder", screen: "/production/work-orders" },
  ];
}
