import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const ROUTING: FieldDef["ref"] = { endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A blown film for liners: its gauge, width and blend. */
export default function FilmSpecForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/film-specifications/"
      back="/making/films"
      backLabel="Film specifications"
      newTitle="New film specification"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_filmspecification", change: "manufacturing.change_filmspecification", delete: "manufacturing.delete_filmspecification" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "film_item", label: "Film item", kind: "pick", pick: ITEM, hint: "The film roll this makes, stocked by weight" },
        { key: "micron", label: "Micron", kind: "decimal", places: 2, hint: "Thickness of one layer of the tube" },
        { key: "lay_flat_width_cm", label: "Lay flat width cm", kind: "decimal", places: 2 },
        { key: "base_polymer", label: "Base polymer", kind: "pick", pick: ITEM, hint: "LDPE" },
        { key: "lldpe_item", label: "LLDPE item", kind: "pick", pick: ITEM, hint: "LLDPE, for toughness and seal strength" },
        { key: "lldpe_percent", label: "LLDPE %", kind: "decimal", places: 3, initial: "0" },
        { key: "masterbatch_item", label: "Masterbatch item", kind: "pick", pick: ITEM },
        { key: "masterbatch_percent", label: "Masterbatch %", kind: "decimal", places: 3, initial: "0" },
        { key: "extrusion_waste_percent", label: "Extrusion waste %", kind: "decimal", places: 3, initial: "3", hint: "Of what is fed in" },
        { key: "waste_item", label: "Waste item", kind: "pick", pick: ITEM, hint: "What the collected film waste is stocked as" },
        { key: "waste_recovered_percent", label: "Waste recovered %", kind: "decimal", places: 3, initial: "80" },
        { key: "micron_tolerance_percent", label: "Micron tolerance %", kind: "decimal", places: 2, initial: "10", hint: "How far a gauge reading may be from the thickness" },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING },
        { key: "valid_from", label: "From", kind: "date", hint: "The first date output made to this specification is due" },
        { key: "valid_to", label: "To", kind: "date", hint: "The last date output made to this specification is due" },
        { key: "bom", label: "Recipe it writes", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "grams_per_metre", label: "Grams per metre", readOnly: true },
      ]}
    />
  );
}
