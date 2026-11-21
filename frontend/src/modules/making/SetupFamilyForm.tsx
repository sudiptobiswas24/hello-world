import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, WORK_CENTRE } from "./refs";

/** Which family an item belongs to on a bank: changing between families is what costs a changeover. */
export default function SetupFamilyForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/setup-families/"
      back="/making/setup-families"
      backLabel="Setup families"
      newTitle="New setup family"
      heading={(row) => `${String(row.item_label)} on ${String(row.work_centre_name)}`}
      permissions={{ add: "manufacturing.add_setupfamily", change: "manufacturing.change_setupfamily",
        delete: "manufacturing.delete_setupfamily" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM, show: (row) => String(row.item_label) },
        { key: "work_centre", label: "On", kind: "ref", ref: WORK_CENTRE },
        { key: "family", label: "Family", hint: "e.g. a colour, a width, a denier" },
      ]}
    />
  );
}
