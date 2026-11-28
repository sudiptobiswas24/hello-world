import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;
type Line = Row & { id: number };

const COUNTRY: FieldDef["ref"] = { endpoint: "/api/core/countries/", permission: "core.view_country", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const TAX: FieldDef["ref"] = { endpoint: "/api/accounting/taxes/", permission: "accounting.view_tax", label: (row: Row) => String(row.name || row.code) };

/** Which tax replaces which for a kind of customer or vendor: an export, a unit in a special zone. */
export default function FiscalPositionForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/fiscal-positions/"
      back="/settings/fiscal-positions"
      backLabel="Fiscal positions"
      newTitle="New fiscal position"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_fiscalposition", change: "accounting.change_fiscalposition", delete: "accounting.delete_fiscalposition" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "country", label: "Country", kind: "ref", ref: COUNTRY },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Taxes it replaces", permission: "accounting.view_fiscalpositiontaxmapping", endpoint: "", query: () => ({}),
        rows: (record) => (record.tax_mappings as Line[]) ?? [],
        columns: [
          { key: "source_tax_name", label: "Instead of" },
          { key: "target_tax_name", label: "Charge", render: (row) => String(row.target_tax_name || "No tax") },
        ],
        adder: { label: "Replace a tax", permission: "accounting.add_fiscalpositiontaxmapping",
          url: () => "/api/accounting/fiscal-position-tax-mappings/",
          fields: [
            { key: "source_tax", label: "Instead of", kind: "ref", ref: TAX },
            { key: "target_tax", label: "Charge", kind: "ref", ref: TAX, hint: "Leave empty to charge no tax, as on an export under bond" },
          ],
          body: (values, record) => ({ ...values, fiscal_position: record.id }) },
        remover: { permission: "accounting.delete_fiscalpositiontaxmapping",
          url: (row) => `/api/accounting/fiscal-position-tax-mappings/${row.id}/` },
      }]}
    />
  );
}
