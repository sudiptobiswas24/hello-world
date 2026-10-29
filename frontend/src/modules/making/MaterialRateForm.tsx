import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };

/** What a material is costed at in a quotation from a date: a dated rate sheet, so a quote made last month keeps last month's price. */
export default function MaterialRateForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/material-rates/"
      back="/making/material-rates"
      backLabel="Material rates"
      newTitle="New material rate"
      heading={(row) => String(row.item_label ?? "") + " · " + String(row.valid_from ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_materialrate", change: "manufacturing.change_materialrate", delete: "manufacturing.delete_materialrate" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM },
        { key: "rate", label: "Rate", kind: "decimal", hint: "Per unit the item is stocked in — per kilogramme for polymer" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "note", label: "Note" },
      ]}
    />
  );
}
