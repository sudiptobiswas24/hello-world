import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem" },
  { key: "date", label: "Paid", kind: "date", width: "8rem" },
  { key: "payment_number", label: "Payment", width: "10rem" },
  { key: "bill_number", label: "Bill", width: "10rem" },
  { key: "amount", label: "Paid", kind: "money", width: "10rem" },
  { key: "igst", label: "IGST", kind: "money", width: "8rem" },
  { key: "cgst", label: "CGST", kind: "money", width: "8rem" },
  { key: "sgst", label: "SGST", kind: "money", width: "8rem" },
  { key: "cancelled_on", label: "", width: "7rem", render: (row) => (row.cancelled_on ? "Cancelled" : "") },
];

/** Payment vouchers for supplies under reverse charge (s.31(3)(g)). */
export default function PaymentVouchers() {
  return (
    <ListView<Row>
      title="Payment vouchers"
      noun={["payment voucher", "payment vouchers"]}
      endpoint="/api/gst/payment-vouchers/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/payment-vouchers/${row.id}`}
      searchHint="Number, bill or payment"
      create={{ href: "/accounts/payment-vouchers/new", permission: "gst.add_paymentvoucher" }}
    />
  );
}
