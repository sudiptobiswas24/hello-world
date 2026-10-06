import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const TAX: FieldDef["ref"] = { endpoint: "/api/accounting/taxes/", permission: "accounting.view_tax", label: (row: Row) => String(row.name || row.code) };
const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** Something billed that is not stock: freight, loading, a rush surcharge. Where it lands each way and the tax it carries by default. */
export default function ChargeTypeForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/charge-types/"
      back="/settings/charge-types"
      backLabel="Charge types"
      newTitle="New charge type"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_chargetype", change: "accounting.change_chargetype", delete: "accounting.delete_chargetype" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "revenue_account", label: "Revenue account", kind: "pick", pick: ACCOUNT, hint: "Where this lands when charged to a customer" },
        { key: "expense_account", label: "Expense account", kind: "pick", pick: ACCOUNT, hint: "Where this lands when a vendor charges it to the company" },
        { key: "capitalise_into_inventory", label: "Capitalise into inventory", kind: "bool", initial: false, hint: "Inbound only" },
        { key: "hsn_code", label: "HSN code", hint: "SAC for a service charge — 9965 for goods transport, say" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Taxes it carries", permission: "accounting.view_chargetype", endpoint: "", query: () => ({}),
        rows: (record) => ((record.tax_rows as Row[]) ?? []).map((tax) => ({ ...tax, id: Number(tax.id), charge: record.id })),
        columns: [{ key: "name", label: "Tax" }],
        adder: {
          label: "Add a tax", permission: "accounting.change_chargetype",
          url: (record) => `/api/accounting/charge-types/${String(record.id)}/taxes/`,
          fields: [{ key: "tax", label: "Tax", kind: "ref", ref: TAX }],
          body: (values) => values,
        },
        remover: {
          permission: "accounting.change_chargetype",
          url: (row) => `/api/accounting/charge-types/${String(row.charge)}/taxes/?tax=${String(row.id)}`,
        },
      }]}
    />
  );
}
