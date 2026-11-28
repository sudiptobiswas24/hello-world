import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { KINDS, REP_PICK } from "./crmRefs";
import { CUSTOMER } from "./extraRefs";

type Row = Record<string, unknown>;

const LEAD_PICK: FieldDef["pick"] = {
  endpoint: "/api/sales/leads/", permission: "sales.view_lead", label: (row: Row) => `${String(row.number)} · ${String(row.company_name)}`,
};
const OPPORTUNITY_PICK: FieldDef["pick"] = {
  endpoint: "/api/sales/opportunities/", permission: "sales.view_opportunity", label: (row: Row) => `${String(row.number)} · ${String(row.title)}`,
};

/** One call, visit or note, about one lead, opportunity or customer, with the day to follow it up. */
export default function ActivityForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/activities/"
      back="/sales/activities"
      backLabel="Calls and visits"
      newTitle="Log a call or visit"
      heading={(row) => String(row.summary ?? "")}
      state={(row) => (row.done_on ? { label: "done", tone: "done" } : row.due_on ? { label: "to do", tone: "open" } : null)}
      permissions={{ add: "sales.add_activity", change: "sales.change_activity", delete: "sales.delete_activity" }}
      fields={[
        { key: "kind", label: "Kind", kind: "choice", choices: KINDS, initial: "call" },
        { key: "lead", label: "About a lead", kind: "pick", pick: LEAD_PICK, hint: "One of the three" },
        { key: "opportunity", label: "Or an opportunity", kind: "pick", pick: OPPORTUNITY_PICK },
        { key: "party", label: "Or a customer", kind: "pick", pick: CUSTOMER },
        { key: "summary", label: "What was said or done", wide: true },
        { key: "notes", label: "Notes", kind: "textarea", wide: true },
        { key: "due_on", label: "Follow up on", kind: "date" },
        { key: "owner", label: "Who", kind: "pick", pick: REP_PICK, show: (row) => String(row.owner_name || "") },
        { key: "done_on", label: "Done on", kind: "date", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Done", path: "done", permission: "sales.change_activity", when: (row) => !row.done_on, primary: true, done: "Done" },
      ]}
    />
  );
}
