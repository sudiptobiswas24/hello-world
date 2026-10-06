import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** How far ahead planning looks, its fences and lead times, and who raises the requisitions it proposes. */
export default function PlanningSettingsForm() {
  return (
    <RecordScreen
      endpoint="/api/planning/settings/"
      back="/production/planning-settings"
      backLabel="Planning settings"
      newTitle="New planning settings"
      heading={(row) => String(row.horizon_days ?? "")}
      permissions={{ add: "planning.add_planningsettings", change: "planning.change_planningsettings", delete: "planning.delete_planningsettings" }}
      fields={[
        { key: "horizon_days", label: "Horizon days", kind: "integer", initial: 90, hint: "How far ahead to plan" },
        { key: "default_buy_lead_days", label: "Default buy lead days", kind: "integer", hint: "Used when no agreed vendor price names a lead time" },
        { key: "default_make_lead_days", label: "Default make lead days", kind: "integer", hint: "Used when a bill of materials has no routing, so nothing can compute how long a run takes" },
        { key: "queue_days", label: "Queue days", kind: "integer", initial: 0, hint: "Added to every computed run time to allow for waiting for a machine" },
        { key: "planning_fence_days", label: "Planning fence days", kind: "integer", initial: 0, hint: "The frozen zone, in the plant's working days from the day the plan runs" },
        { key: "demand_fence_days", label: "Demand fence days", kind: "integer", initial: 0, hint: "Inside this many days only real orders count" },
        { key: "reschedule_tolerance_days", label: "Reschedule tolerance days", kind: "integer", initial: 2, hint: "How far out a date has to be before the plan says so" },
        { key: "working_days", label: "Working days", initial: "1234567", hint: "Which days this plant works, as ISO weekday numbers — 1 for Monday through 7 for Sunday" },
        { key: "holiday_region", label: "Holiday region", hint: "Which public holiday list shuts this plant" },
        { key: "requisition_requester", label: "Requisition requester", kind: "pick", pick: PARTY, hint: "Whose name a firmed buy is raised in" },
      ]}
    />
  );
}
