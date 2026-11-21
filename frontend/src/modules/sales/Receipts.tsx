import { PaymentList } from "../money/PaymentList";

export default function Receipts() {
  return <PaymentList direction="receipt" title="Money received" base="/sales/receipts" noun={["receipt", "receipts"]} party="customer" />;
}
