import { PaymentForm, type MoneyConfig } from "../money/PaymentForm";

const RECEIVED: MoneyConfig = {
  direction: "receipt", role: "customer", base: "/sales/receipts", plural: "Money received",
  noun: "receipt", dateLabel: "Received on", field: "invoice", documents: "/api/sales/invoices/",
  documentHref: (id) => `/sales/invoices/${id}`, allocations: "/api/sales/invoice-payments/", app: "sales",
};

/** Money a customer paid, and the invoices it settles. */
export default function ReceiptForm() {
  return <PaymentForm config={RECEIVED} />;
}
