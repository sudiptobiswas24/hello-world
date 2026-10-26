import { RecordScreen } from "../../views/RecordScreen";
import { KEPT_ACCOUNT, MONEY_ACCOUNT } from "../purchasing/refs";

/**
 * A month's reverse-charge tax paid in cash: deposited into the electronic cash ledger and set off against
 * what the bills owe, never against input credit. A challan that did not go through is voided.
 */
export default function GstChallanForm() {
  return (
    <RecordScreen
      endpoint="/api/gst/cash-payments/"
      createUrl="/api/gst/cash-payments/pay/"
      back="/accounts/gst-challans"
      backLabel="GST cash challans"
      newTitle="Pay reverse charge in cash"
      heading={(row) => `CPIN ${String(row.cpin ?? "")}`}
      state={(row) => (row.voided ? { label: "Voided", tone: "void" } : { label: "Paid", tone: "done" })}
      permissions={{ add: "gst.add_gstcashpayment" }}
      fields={[
        { key: "month", label: "Tax of", kind: "date", hint: "Any day of the month; all of its reverse charge is paid" },
        { key: "date", label: "Paid on", kind: "date" },
        { key: "bank_account", label: "From", kind: "pick", pick: MONEY_ACCOUNT },
        { key: "cash_ledger_account", label: "Cash ledger account", kind: "pick", pick: KEPT_ACCOUNT },
        { key: "cpin", label: "CPIN", hint: "The challan's fourteen digits" },
        { key: "igst", label: "IGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cgst", label: "CGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "sgst", label: "SGST", kind: "money", existingOnly: true, readOnly: true },
        { key: "cess", label: "Cess", kind: "money", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: "purchasing.post_bill", danger: true, when: (row) => !row.voided,
          done: "Voided: the tax is owed again" },
      ]}
    />
  );
}
