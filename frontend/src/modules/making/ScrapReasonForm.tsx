import { RecordScreen } from "../../views/RecordScreen";

/** Why material was scrapped, as the floor chooses it. Retire one rather than delete it once used. */
export default function ScrapReasonForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/scrap-reasons/"
      back="/making/scrap-reasons"
      backLabel="Scrap reasons"
      newTitle="New scrap reason"
      heading={(row) => String(row.name)}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_scrapreason", change: "manufacturing.change_scrapreason",
        delete: "manufacturing.delete_scrapreason" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
