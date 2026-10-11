import { RecordScreen } from "../../views/RecordScreen";

export default function ReasonForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/adjustment-reasons/"
      back="/stores/reasons"
      backLabel="Adjustment reasons"
      newTitle="New adjustment reason"
      heading={(row) => String(row.name)}
      permissions={{ add: "inventory.add_adjustmentreason", change: "inventory.change_adjustmentreason" }}
      fields={[
        { key: "code", label: "Code", createOnly: true },
        { key: "name", label: "Reason" },
        { key: "direction", label: "Way", kind: "choice", choices: [["both", "Either"], ["increase", "On only"], ["decrease", "Off only"]] },
        { key: "account", label: "Account", kind: "ref", ref: {
          endpoint: "/api/accounting/accounts/", permission: "accounting.view_account",
          label: (row) => `${String(row.code)} · ${String(row.name)}` } },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
