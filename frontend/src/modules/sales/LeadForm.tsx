import { RecordScreen } from "../../views/RecordScreen";
import { activitiesPanel, CAMPAIGN_REF, REP_PICK, SOURCES } from "./crmRefs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "new" || row.status === "working";

/**
 * One enquiry: who asked, for what, from where. Converted, it becomes a
 * customer the rep carries and its first opportunity; lost, it says
 * why. Either way it then stands as it was.
 */
export default function LeadForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/leads/"
      back="/sales/leads"
      backLabel="Leads"
      newTitle="New lead"
      heading={(row) => `${String(row.number ?? "")} · ${String(row.company_name ?? "")}`}
      state={(row) => (row.status ? { label: String(row.status), tone: row.status === "converted" ? "done" : row.status === "lost" ? "cancelled" : row.status === "working" ? "open" : "draft" } : null)}
      permissions={{ add: "sales.add_lead", change: "sales.change_lead", delete: "sales.delete_lead" }}
      editable={open}
      fields={[
        { key: "company_name", label: "Who asked" },
        { key: "contact_name", label: "Contact" },
        { key: "phone", label: "Phone" },
        { key: "email", label: "Email" },
        { key: "city", label: "City" },
        { key: "state", label: "State" },
        { key: "source", label: "From", kind: "choice", choices: SOURCES, initial: "other" },
        { key: "campaign", label: "Campaign", kind: "ref", ref: CAMPAIGN_REF },
        { key: "interest", label: "What they asked for", wide: true, hint: "50 kg cement sacks, twenty thousand a month" },
        { key: "owner", label: "Rep", kind: "pick", pick: REP_PICK, hint: "A rep's leads are their own; empty, any rep may take it", show: (row) => String(row.owner_name || "Nobody yet") },
        { key: "status", label: "Status", kind: "choice", choices: [["new", "New"], ["working", "Being worked"]], initial: "new" },
        { key: "converted_party_name", label: "Became", readOnly: true, existingOnly: true },
        { key: "converted_on", label: "On", kind: "date", readOnly: true, existingOnly: true },
        { key: "lost_reason", label: "Lost because", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Take it", path: "take", permission: "sales.change_lead", when: (row) => open(row) && !row.owner, done: "Yours" },
        { label: "Convert to a customer", path: "convert", permission: "sales.add_opportunity", when: open, primary: true,
          done: "Customer made, with its first opportunity",
          fields: [
            { key: "code", label: "Customer code", hint: "Short and unique, as the office types it" },
            { key: "name", label: "Name", hint: "Empty takes the company name as asked" },
          ],
          then: (result) => `/sales/opportunities/${String(result.opportunity)}` },
        { label: "Lost", path: "lose", permission: "sales.change_lead", when: open, danger: true, done: "Lost",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[activitiesPanel("lead", open)]}
    />
  );
}
