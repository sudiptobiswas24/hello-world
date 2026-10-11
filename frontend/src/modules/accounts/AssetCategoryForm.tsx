import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A kind of asset: the accounts it is booked to, and how and over how long it is depreciated. */
export default function AssetCategoryForm() {
  return (
    <RecordScreen
      endpoint="/api/assets/categories/"
      back="/accounts/categories"
      backLabel="Asset categories"
      newTitle="New asset category"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "assets.add_assetcategory", change: "assets.change_assetcategory", delete: "assets.delete_assetcategory" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "asset_account", label: "Asset account", kind: "pick", pick: ACCOUNT, hint: "What the asset cost (an asset)" },
        { key: "accumulated_account", label: "Accumulated account", kind: "pick", pick: ACCOUNT, hint: "Depreciation charged to date (a contra-asset)" },
        { key: "expense_account", label: "Expense account", kind: "pick", pick: ACCOUNT, hint: "This period's depreciation (an expense)" },
        { key: "disposal_account", label: "Disposal account", kind: "pick", pick: ACCOUNT, hint: "Gain or loss on disposal" },
        { key: "default_life_months", label: "Default life months", kind: "integer", initial: 60 },
        { key: "method", label: "Method", kind: "choice", choices: [["straight_line", "Straight line"], ["none", "Not depreciated"]], initial: "straight_line" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
