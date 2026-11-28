import type { FieldDef, PanelDef } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** A rep is an employee party; the server holds a limited rep to themselves and checks the pick is an active rep. */
export const REP_PICK: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "employee" },
  label: (row: Row) => String(row.name),
};
export const CAMPAIGN_REF: FieldDef["ref"] = {
  endpoint: "/api/sales/campaigns/", permission: "sales.view_campaign", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
export const KINDS: [string, string][] = [["call", "Call"], ["visit", "Visit"], ["email", "Email or message"], ["note", "Note"], ["follow_up", "Follow-up"]];
export const SOURCES: [string, string][] = [["referral", "Referral"], ["walk_in", "Walked in"], ["phone", "Phone or WhatsApp"], ["web", "Website"], ["exhibition", "Exhibition"], ["campaign", "Campaign"], ["other", "Other"]];
export const STAGE_TONES: Record<string, string> = { new: "draft", qualified: "open", quoted: "confirmed", won: "done", lost: "cancelled" };
export const LEAD_TONES: Record<string, string> = { new: "draft", working: "open", converted: "done", lost: "cancelled" };
export const CHANNELS: [string, string][] = [["exhibition", "Exhibition"], ["print", "Print"], ["digital", "Digital"], ["field", "Field visits"], ["referral", "Referral drive"], ["other", "Other"]];

/** The calls, visits and notes against one lead, opportunity or customer, and a follow-up to add. */
export function activitiesPanel(about: "lead" | "opportunity" | "party", openWhen?: (record: Row) => boolean): PanelDef {
  return {
    title: "Calls and visits", permission: "sales.view_activity",
    endpoint: "/api/sales/activities/", query: (record) => ({ [about]: String(record.id) }),
    columns: [
      { key: "due_on", label: "Due", kind: "date", width: "8rem" },
      { key: "kind", label: "", kind: "status", width: "7rem" },
      { key: "summary", label: "What" },
      { key: "owner_name", label: "Who", width: "10rem" },
      { key: "done_on", label: "Done", kind: "date", width: "8rem" },
    ],
    rowAction: { label: "Done", permission: "sales.change_activity", when: (row) => !row.done_on,
      url: (row) => `/api/sales/activities/${row.id}/done/`, done: "Done" },
    adder: { label: "Log a call or visit", permission: "sales.add_activity", when: openWhen,
      url: () => "/api/sales/activities/",
      fields: [
        { key: "kind", label: "Kind", kind: "choice", choices: KINDS, initial: "call" },
        { key: "summary", label: "What was said or done", kind: "text" },
        { key: "due_on", label: "Follow up on", kind: "date", hint: "Empty for nothing to follow up" },
      ],
      body: (values, record) => ({ [about]: record.id, kind: values.kind, summary: values.summary, due_on: values.due_on || null }) },
  };
}
