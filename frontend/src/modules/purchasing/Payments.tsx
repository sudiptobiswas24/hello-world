import { PaymentList } from "../money/PaymentList";

export default function Payments() {
  return <PaymentList direction="disbursement" title="Money paid" base="/purchasing/payments" noun={["payment", "payments"]} party="vendor" />;
}
