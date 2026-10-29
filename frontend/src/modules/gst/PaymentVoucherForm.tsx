import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PAYMENT = {
  endpoint: "/api/accounting/payments/", permission: "accounting.view_payment",
  query: { posted: "true", direction: "disbursement" },
  label: (row: Row) => `${String(row.number)} · ${String(row.party_name ?? "")}`,
};

/** The voucher issued with a payment for a reverse-charge supply; cancelled when the payment is voided. */
export default function PaymentVoucherForm() {
  return (
    <RecordScreen
      endpoint="/api/gst/payment-vouchers/"
      back="/accounts/payment-vouchers"
      backLabel="Payment vouchers"
      newTitle="Issue payment vouchers"
      heading={(row) => String(row.number ?? "")}
      state={(row) => (row.cancelled_on ? { label: "Cancelled", tone: "void" } : { label: "Issued", tone: "done" })}
      permissions={{ add: "gst.add_paymentvoucher" }}
      note={() => "One voucher is issued for each reverse-charge bill the payment settles."}
      fields={[
        { key: "payment", label: "For payment", kind: "pick", pick: PAYMENT, show: (row) => String(row.payment_number ?? "") },
        { key: "bill_number", label: "Bill", existingOnly: true, readOnly: true },
        { key: "date", label: "Dated", kind: "date", existingOnly: true, readOnly: true },
        { key: "amount", label: "Paid", kind: "money", existingOnly: true, readOnly: true },
        { key: "igst", label: "IGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cgst", label: "CGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "sgst", label: "SGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cess", label: "Cess", kind: "money", existingOnly: true, readOnly: true },
        { key: "cancelled_on", label: "Cancelled on", kind: "date", existingOnly: true, readOnly: true },
      ]}
    />
  );
}
