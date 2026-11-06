import { RecordScreen } from "../../views/RecordScreen";
import { WORK_CENTRE } from "./refs";

/** What changing a bank from one family to another costs, in minutes and in purged material. */
export default function ChangeoverRuleForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/changeover-rules/"
      back="/making/changeovers"
      backLabel="Changeover rules"
      newTitle="New changeover rule"
      heading={(row) => `${String(row.work_centre_name)}: ${String(row.from_family || "any")} to ${String(row.to_family || "any")}`}
      permissions={{ add: "manufacturing.add_changeoverrule", change: "manufacturing.change_changeoverrule",
        delete: "manufacturing.delete_changeoverrule" }}
      fields={[
        { key: "work_centre", label: "On", kind: "ref", ref: WORK_CENTRE },
        { key: "from_family", label: "From family", hint: "Empty: from any" },
        { key: "to_family", label: "To family", hint: "Empty: to any" },
        { key: "minutes", label: "Minutes", kind: "decimal" },
        { key: "purge_kg", label: "Purge kg", kind: "decimal", places: 3, initial: "0" },
        { key: "notes", label: "Notes", wide: true },
      ]}
    />
  );
}
