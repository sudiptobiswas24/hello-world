import { today } from "../../forms/fields";
import { RecordScreen } from "../../views/RecordScreen";
import { PARTY } from "../accounts/refs";

/** Who supplies labour to the plant, under what licence and for how many at once: the register of contractors (Form XII), with the workers each brought (Form XIII). */
export default function LabourContractorForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/labour-contractors/"
      back="/payroll/labour-contractors"
      backLabel="Labour contractors"
      newTitle="New contractor"
      heading={(row) => String(row.party_name ?? "") + " · " + String(row.licence_number ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "hr.add_labourcontractor", change: "hr.change_labourcontractor" }}
      fields={[
        { key: "party", label: "Contractor", kind: "pick", pick: PARTY, createOnly: true },
        { key: "licence_number", label: "Licence number" },
        { key: "licence_valid_to", label: "Licence valid to", kind: "date" },
        { key: "work_nature", label: "Work nature", hint: "Loom operation, bag stitching, loading" },
        { key: "max_workers", label: "Max workers", kind: "integer", hint: "As many as the licence allows at once" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Workers", permission: "hr.view_contractworker", endpoint: "/api/hr/contract-workers/",
        query: (contractor) => ({ contractor: String(contractor.id) }),
        columns: [
          { key: "name", label: "Name" },
          { key: "designation", label: "Work", width: "10rem" },
          { key: "daily_wage", label: "Daily wage", kind: "money", width: "8rem" },
          { key: "joined_on", label: "Joined", kind: "date", width: "8rem" },
          { key: "left_on", label: "Left", kind: "date", width: "8rem" },
        ],
        adder: {
          label: "Add a worker", permission: "hr.add_contractworker", url: () => "/api/hr/contract-workers/",
          fields: [
            { key: "name", label: "Name" },
            { key: "gender", label: "Gender", kind: "choice", choices: [["female", "Female"], ["male", "Male"], ["other", "Other"]] },
            { key: "designation", label: "Work" },
            { key: "daily_wage", label: "Daily wage", kind: "money" },
            { key: "joined_on", label: "Joined", kind: "date" },
          ],
          body: (values, contractor) => ({ ...values, contractor: contractor.id }),
        },
        rowActions: [{
          label: "Left today", permission: "hr.change_contractworker", method: "PATCH", done: "Marked as left",
          when: (row) => !row.left_on, url: (row) => `/api/hr/contract-workers/${String(row.id)}/`,
          body: () => ({ left_on: today() }),
        }],
      }]}
    />
  );
}
