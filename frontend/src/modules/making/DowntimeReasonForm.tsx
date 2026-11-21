import { RecordScreen } from "../../views/RecordScreen";

/** Why a machine stopped. A planned one (a changeover, a service) is not lost availability. */
export default function DowntimeReasonForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/downtime-reasons/"
      back="/making/stoppage-reasons"
      backLabel="Stoppage reasons"
      newTitle="New stoppage reason"
      heading={(row) => String(row.name)}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_downtimereason", change: "manufacturing.change_downtimereason",
        delete: "manufacturing.delete_downtimereason" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "is_planned", label: "Planned", kind: "bool", hint: "A changeover or a service, not a loss" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
