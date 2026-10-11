import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const ROUTING: FieldDef["ref"] = { endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A tape as the extruder makes it: its denier and width, and the blend it is drawn from. Saved, it writes the recipe the plant runs. */
export default function TapeSpecForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/tape-specifications/"
      back="/making/tapes"
      backLabel="Tape specifications"
      newTitle="New tape specification"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_tapespecification", change: "manufacturing.change_tapespecification", delete: "manufacturing.delete_tapespecification" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "tape_item", label: "Tape item", kind: "pick", pick: ITEM, hint: "The tape this makes, stocked and valued by weight" },
        { key: "denier", label: "Denier", kind: "decimal", places: 2, hint: "Grammes per 9,000 metres of tape" },
        { key: "tape_width_mm", label: "Tape width mm", kind: "decimal", places: 3, hint: "Slit width" },
        { key: "draw_ratio", label: "Draw ratio", kind: "decimal", places: 2, hint: "How far the tape is stretched in the orientation oven" },
        { key: "virgin_granule", label: "Virgin granule", kind: "pick", pick: ITEM, hint: "The PP homopolymer" },
        { key: "regrind_item", label: "Regrind item", kind: "pick", pick: ITEM, hint: "Reprocessed plant waste" },
        { key: "regrind_percent", label: "Regrind %", kind: "decimal", places: 3, initial: "0", hint: "How much of the blend is reprocessed material" },
        { key: "filler_item", label: "Filler item", kind: "pick", pick: ITEM, hint: "Calcium carbonate masterbatch" },
        { key: "filler_percent", label: "Filler %", kind: "decimal", places: 3, initial: "0" },
        { key: "masterbatch_item", label: "Masterbatch item", kind: "pick", pick: ITEM, hint: "Colour concentrate" },
        { key: "masterbatch_percent", label: "Masterbatch %", kind: "decimal", places: 3, initial: "0" },
        { key: "uv_item", label: "UV item", kind: "pick", pick: ITEM, hint: "UV stabiliser concentrate, for sacks that will stand in a yard" },
        { key: "uv_percent", label: "UV %", kind: "decimal", places: 3, initial: "0" },
        { key: "extrusion_waste_percent", label: "Extrusion waste %", kind: "decimal", places: 3, initial: "3", hint: "Of what is fed in" },
        { key: "waste_recovered_percent", label: "Waste recovered %", kind: "decimal", places: 3, initial: "80", hint: "How much of that loss is collected and reground rather than burnt off, swept up or…" },
        { key: "denier_tolerance_percent", label: "Denier tolerance %", kind: "decimal", places: 2, initial: "5", hint: "How far the tape may be from its denier before a batch of it is out of specification" },
        { key: "min_tenacity_gpd", label: "Min tenacity gpd", kind: "decimal", places: 2, hint: "The least strength a batch may have, in grammes per denier" },
        { key: "elongation_min_percent", label: "Elongation min %", kind: "decimal", places: 2, hint: "How far the tape must stretch before it breaks, at least" },
        { key: "elongation_max_percent", label: "Elongation max %", kind: "decimal", places: 2, hint: "And at most" },
        { key: "bom", label: "Recipe it writes", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "virgin_percent", label: "Virgin percent", readOnly: true },
        { key: "metres_per_kg", label: "Metres per kg", readOnly: true },
        { key: "valid_from", label: "From", kind: "date", hint: "The first date output made to this specification is due" },
        { key: "valid_to", label: "To", kind: "date", hint: "The last date output made to this specification is due" },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING, hint: "The machines this passes through" },
      ]}
    />
  );
}
