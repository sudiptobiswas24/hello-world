import { PaymentForm as MoneyForm, type MoneyConfig } from "../money/PaymentForm";

const PAID: MoneyConfig = {
  direction: "disbursement", role: "vendor", base: "/purchasing/payments", plural: "Money paid",
  noun: "payment", dateLabel: "Paid on", field: "bill", documents: "/api/purchasing/bills/",
  documentHref: (id) => `/purchasing/bills/${id}`, allocations: "/api/purchasing/bill-payments/", app: "purchasing",
  account: "payable_account",
};

/** Money paid to a vendor, and the bills it settles. */
export default function PaymentForm() {
  return <MoneyForm config={PAID} />;
}
