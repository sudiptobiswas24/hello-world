import type { SmartDef } from "../../views/SmartButtons";

/** What has passed between us and a vendor, each opening its list. */
export function vendorButtons(id: number): SmartDef[] {
  const vendor = String(id);
  return [
    { label: "Purchase orders", endpoint: "/api/purchasing/purchase-orders/", query: { vendor },
      permission: "purchasing.view_purchaseorder", screen: "/purchasing/orders" },
    { label: "To receive", endpoint: "/api/purchasing/purchase-orders/", query: { vendor, to_receive: "true" },
      permission: "purchasing.view_purchaseorder", screen: "/purchasing/orders" },
    { label: "Goods in", endpoint: "/api/purchasing/goods-receipts/", query: { purchase_order__vendor: vendor },
      permission: "purchasing.view_goodsreceipt", screen: "/purchasing/goods-in" },
    { label: "Not yet paid", endpoint: "/api/purchasing/bills/", query: { vendor, open: "true" },
      permission: "purchasing.view_bill", screen: "/purchasing/bills", urgent: true },
    { label: "Money paid", endpoint: "/api/accounting/payments/", query: { party: vendor, direction: "disbursement" },
      permission: "accounting.view_payment", screen: "/purchasing/payments" },
  ];
}

/** What a purchase order has led to. */
export function purchaseOrderButtons(id: number): SmartDef[] {
  const order = String(id);
  return [
    { label: "Goods in", endpoint: "/api/purchasing/goods-receipts/", query: { purchase_order: order },
      permission: "purchasing.view_goodsreceipt", screen: "/purchasing/goods-in" },
    { label: "Bills", endpoint: "/api/purchasing/bills/", query: { purchase_order: order }, permission: "purchasing.view_bill", screen: "/purchasing/bills" },
  ];
}
