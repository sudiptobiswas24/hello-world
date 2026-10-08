import { RecordScreen } from "../../views/RecordScreen";
import { MONEY_ACCOUNT, SECTION } from "./refs";

/**
 * A month's deductions under one section paid over: everything deducted
 * then and not yet paid goes on it. Paid is a fact; a challan that did not
 * go through is voided, and its deductions wait for the next.
 */
export default function TdsChallanForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/tds-challans/"
      createUrl="/api/purchasing/tds-challans/pay/"
      back="/accounts/tds-challans"
      backLabel="TDS challans"
      newTitle="Pay TDS over"
      heading={(row) => `${String(row.section_code ?? "")} · ${String(row.bsr_code ?? "")}/${String(row.challan_number ?? "")}`}
      state={(row) => (row.voided ? { label: "Voided", tone: "void" } : { label: "Paid", tone: "done" })}
      permissions={{ add: "purchasing.add_tdschallan" }}
      fields={[
        { key: "section", label: "Section", kind: "ref", ref: SECTION, show: (row) => String(row.section_code) },
        { key: "month", label: "Deducted in", kind: "date", hint: "Any day of the month; all of it is paid" },
        { key: "date", label: "Paid on", kind: "date" },
        { key: "bank_account", label: "From", kind: "pick", pick: MONEY_ACCOUNT },
        { key: "challan_number", label: "Challan number" },
        { key: "bsr_code", label: "BSR code", hint: "The bank branch's seven digits" },
        { key: "amount", label: "Amount", kind: "money", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: "purchasing.add_tdschallan", danger: true, when: (row) => !row.voided,
          done: "Voided: the tax is owed again" },
      ]}
    />
  );
}
