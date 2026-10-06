import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A customer's artwork: its colours, when it was approved and by whom, and how long its cylinders take to engrave. */
export default function PrintDesignForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/print-designs/"
      back="/making/designs"
      backLabel="Print designs"
      newTitle="New print design"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_printdesign", change: "manufacturing.change_printdesign", delete: "manufacturing.delete_printdesign" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "customer", label: "Customer", kind: "pick", pick: PARTY, hint: "Whose artwork it is" },
        { key: "colours", label: "Colours", kind: "integer", initial: 1, hint: "How many colours it prints in" },
        { key: "artwork_reference", label: "Artwork reference", hint: "Where the approved file lives" },
        { key: "approved_on", label: "Approved on", kind: "date" },
        { key: "approved_by", label: "Approved by", kind: "pick", pick: PARTY, hint: "Who at the customer signed it off" },
        { key: "engraving_lead_days", label: "Engraving lead days", kind: "integer", hint: "Days from ordering a cylinder to having it at the press" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "is_approved", label: "Approved", readOnly: true },
        { key: "cylinders_short_by", label: "Cylinders short by", readOnly: true },
        { key: "ready_on", label: "Ready on", readOnly: true },
      ]}
    />
  );
}
