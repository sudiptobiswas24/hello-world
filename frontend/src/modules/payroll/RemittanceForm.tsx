import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PAYMENT: FieldDef["pick"] = {
  endpoint: "/api/accounting/payments/", permission: "accounting.view_payment", query: { direction: "disbursement", posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.party_name ?? "")}`,
};
const ACCOUNT: FieldDef["pick"] = {
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};

/**
 * A payment out to PF, ESI or the tax office, said to settle one month of
 * one liability. Recorded, not edited: a wrong one is deleted and made again.
 */
export default function RemittanceForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/statutory-remittances/"
      back="/payroll/remittances"
      backLabel="Statutory remittances"
      newTitle="New remittance"
      heading={(row) => `${String(row.account_name ?? "")} · ${String(row.period ?? "")}`}
      permissions={{ add: "hr.add_statutoryremittance", delete: "hr.delete_statutoryremittance" }}
      fields={[
        { key: "payment", label: "Paid by", kind: "pick", pick: PAYMENT, createOnly: true, show: (row) => String(row.payment_number ?? "") },
        { key: "liability_account", label: "Owed into", kind: "pick", pick: ACCOUNT, createOnly: true, show: (row) => String(row.account_name ?? "") },
        { key: "period", label: "For the month of", kind: "date", createOnly: true, hint: "Any day of the month it settles" },
        { key: "amount", label: "Amount", kind: "money", createOnly: true },
      ]}
    />
  );
}
