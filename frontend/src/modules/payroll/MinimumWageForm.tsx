import { RecordScreen } from "../../views/RecordScreen";

const SKILLS: [string, string][] = [["unskilled", "Unskilled"], ["semi_skilled", "Semi-skilled"], ["skilled", "Skilled"], ["highly_skilled", "Highly skilled"]];

/** One minimum wage from the day it applies. A changed figure waits for the Controller's confirmation again. */
export default function MinimumWageForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/minimum-wages/"
      back="/payroll/minimum-wages"
      backLabel="Minimum wages"
      newTitle="New minimum wage"
      heading={(row) => `${String(row.employment ?? "")} from ${String(row.effective_from ?? "")}`}
      state={(row) => (row.confirmed_on ? { label: "Confirmed", tone: "done" } : { label: "To confirm", tone: "draft" })}
      permissions={{ add: "hr.add_minimumwage", change: "hr.change_minimumwage", delete: "hr.delete_minimumwage" }}
      fields={[
        { key: "state", label: "State", hint: "As the person's work state names it: WB" },
        { key: "employment", label: "Scheduled employment", hint: "As the notification names it" },
        { key: "zone", label: "Zone", hint: "Blank where the notification has none" },
        { key: "skill", label: "Skill", kind: "choice", choices: SKILLS, initial: "unskilled" },
        { key: "effective_from", label: "Applies from", kind: "date" },
        { key: "basic", label: "Basic, a month", kind: "money" },
        { key: "vda", label: "VDA, a month", kind: "money", initial: "0" },
        { key: "source", label: "Source", hint: "The notification" },
        { key: "confirmed_on", label: "Confirmed on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Confirm", path: "confirm", body: (_values, record) => ({ state: record.state, employment: record.employment,
            zone: record.zone, skill: record.skill, effective_from: record.effective_from, basic: record.basic, vda: record.vda }),
          permission: "hr.confirm_minimumwage", when: (row) => !row.confirmed_on, done: "Confirmed: bonus is worked out on it from its day" },
      ]}
    />
  );
}
