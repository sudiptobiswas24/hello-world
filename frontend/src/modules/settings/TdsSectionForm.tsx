import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** The sections tax is deducted under: each one's rate, its rate without a PAN, its threshold and where the tax is owed or claimed. Kept by the plant; they change with every budget. */
export default function TdsSectionForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tds-sections/"
      back="/settings/tds-sections"
      backLabel="TDS sections"
      newTitle="New section"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_tdssection", change: "accounting.change_tdssection", delete: "accounting.delete_tdssection" }}
      fields={[
        { key: "code", label: "Code", hint: "194Q, 194C, 194J" },
        { key: "name", label: "Name" },
        { key: "rate_percent", label: "Rate %", kind: "decimal", places: 4, hint: "Deducted from a party whose PAN is on file" },
        { key: "no_pan_rate_percent", label: "Without PAN %", kind: "decimal", places: 4, hint: "Deducted from a party with no PAN on file (section 206AA)" },
        { key: "mode", label: "Taxed on", kind: "choice", choices: [["excess", "On what the year passes the threshold by (194Q)"], ["whole", "On the whole year once a limit is passed (194C, 194J)"]], initial: "whole" },
        { key: "single_threshold", label: "One bill above", kind: "money", hint: "One bill above this is taxed" },
        { key: "annual_threshold", label: "The year above", kind: "money", hint: "The year's bills from one party above this are taxed" },
        { key: "payable_account", label: "Payable account", kind: "pick", pick: ACCOUNT, hint: "Where tax the company deducts is owed until a challan pays it over" },
        { key: "receivable_account", label: "Receivable account", kind: "pick", pick: ACCOUNT, hint: "Where tax a customer deducted waits to be claimed against Form 26AS" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
