import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const FILMSPECIFICATION: FieldDef["ref"] = { endpoint: "/api/manufacturing/film-specifications/", permission: "manufacturing.view_filmspecification", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ROUTING: FieldDef["ref"] = { endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A liner cut and sealed from a film: its length and what it weighs. */
export default function LinerSpecForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/liner-specifications/"
      back="/making/liners"
      backLabel="Liner specifications"
      newTitle="New liner specification"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_linerspecification", change: "manufacturing.change_linerspecification", delete: "manufacturing.delete_linerspecification" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "liner_item", label: "Liner item", kind: "pick", pick: ITEM, hint: "The liner, counted" },
        { key: "film", label: "Film", kind: "ref", ref: FILMSPECIFICATION },
        { key: "cut_length_cm", label: "Cut length cm", kind: "decimal", places: 2, hint: "Tube cut for one liner, the seal included" },
        { key: "seal_waste_percent", label: "Seal waste %", kind: "decimal", places: 3, initial: "2", hint: "Of the film fed in" },
        { key: "waste_recovered_percent", label: "Waste recovered %", kind: "decimal", places: 3, initial: "80" },
        { key: "weight_tolerance_percent", label: "Weight tolerance %", kind: "decimal", places: 2, initial: "5" },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING },
        { key: "valid_from", label: "From", kind: "date", hint: "The first date output made to this specification is due" },
        { key: "valid_to", label: "To", kind: "date", hint: "The last date output made to this specification is due" },
        { key: "bom", label: "Recipe it writes", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "liner_grams", label: "Liner grams", readOnly: true },
      ]}
    />
  );
}
