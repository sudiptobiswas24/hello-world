import { RecordScreen } from "../../views/RecordScreen";

/** A group of taxes reported together. */
export default function TaxGroupForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tax-groups/"
      back="/settings/tax-groups"
      backLabel="Tax groups"
      newTitle="New tax group"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "accounting.add_taxgroup", change: "accounting.change_taxgroup", delete: "accounting.delete_taxgroup" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
      ]}
    />
  );
}
