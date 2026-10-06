import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const TAXGROUP: FieldDef["ref"] = { endpoint: "/api/accounting/tax-groups/", permission: "accounting.view_taxgroup", label: (row: Row) => String(row.name || row.code) };

/** A tax a line can carry: its rate, how it is computed, and the accounts it posts to. */
export default function TaxForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/taxes/"
      back="/settings/taxes"
      backLabel="Taxes"
      newTitle="New tax"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_tax", change: "accounting.change_tax", delete: "accounting.delete_tax" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "group", label: "Group", kind: "ref", ref: TAXGROUP },
        { key: "computation", label: "Computation", kind: "choice", choices: [["percentage", "Percentage of base"], ["fixed", "Fixed amount per unit"]], initial: "percentage" },
        { key: "rate", label: "Rate", kind: "decimal", hint: "Percentage (20.0000 = 20%) or, for fixed taxes, amount per unit" },
        { key: "price_included", label: "Price included", kind: "bool", initial: false, hint: "The unit price already contains this tax (common in EU retail)" },
        { key: "include_base_amount", label: "Include base amount", kind: "bool", initial: false, hint: "Add this tax to the base used by later taxes in sequence (compound tax)" },
        { key: "sequence", label: "Sequence", kind: "integer", initial: 10, hint: "Application order" },
        { key: "scope", label: "Scope", kind: "choice", choices: [["sales", "Sales only"], ["purchase", "Purchases only"], ["both", "Sales and purchases"]], initial: "both" },
        { key: "collected_account", label: "Collected account", kind: "pick", pick: ACCOUNT, hint: "Sales" },
        { key: "paid_account", label: "Paid account", kind: "pick", pick: ACCOUNT, hint: "Purchases" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "gst_head", label: "GST head", kind: "choice", choices: [["cgst", "Central tax (CGST)"], ["sgst", "State or union territory tax (SGST/UTGST)"], ["igst", "Integrated tax (IGST)"], ["cess", "Compensation cess"], ["other", "Not GST"]], hint: "Which column of a GST return this tax is reported in" },
      ]}
    />
  );
}
