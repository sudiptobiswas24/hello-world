import { RecordScreen } from "../../views/RecordScreen";

/** A label to group customers and vendors by: a region, a trade, a size. */
export default function PartyTagForm() {
  return (
    <RecordScreen
      endpoint="/api/core/party-tags/"
      back="/settings/party-tags"
      backLabel="Party tags"
      newTitle="New party tag"
      heading={(row) => String(row.name ?? "")}
      permissions={{ add: "core.add_partytag", change: "core.change_partytag", delete: "core.delete_partytag" }}
      fields={[
        { key: "name", label: "Name" },
        { key: "description", label: "Description" },
      ]}
    />
  );
}
