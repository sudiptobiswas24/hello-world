import { RecordScreen } from "../../views/RecordScreen";

/** One cost centre: a code, a name, whether it is still in use. The books stamp it on each expense line when the line posts. */
export default function CostCentreForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/cost-centres/"
      trail="accounting.costcentre"
      back="/accounts/cost-centres"
      backLabel="Cost centres"
      newTitle="New cost centre"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      permissions={{ add: "accounting.add_costcentre", change: "accounting.change_costcentre", delete: "accounting.delete_costcentre" }}
      fields={[
        { key: "code", label: "Code", hint: "LOOM, PRINT, OFFICE" },
        { key: "name", label: "Name" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "note", label: "Note", wide: true },
      ]}
    />
  );
}
