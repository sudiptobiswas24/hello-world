import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const ROUTING: FieldDef["ref"] = { endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const TAPESPECIFICATION: FieldDef["ref"] = { endpoint: "/api/manufacturing/tape-specifications/", permission: "manufacturing.view_tapespecification", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A woven fabric: its tapes, its mesh and width, and the grammage those give. Saved, it writes the recipe the looms run. */
export default function FabricSpecForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/fabric-specifications/"
      back="/making/fabrics"
      backLabel="Fabric specifications"
      newTitle="New fabric specification"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_fabricspecification", change: "manufacturing.change_fabricspecification", delete: "manufacturing.delete_fabricspecification" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "fabric_item", label: "Fabric item", kind: "pick", pick: ITEM, hint: "The woven fabric this makes, stocked and valued by weight — rolls are weighed, not…" },
        { key: "warp_tape", label: "Warp tape", kind: "ref", ref: TAPESPECIFICATION, hint: "The tape running along the fabric" },
        { key: "weft_tape", label: "Weft tape", kind: "ref", ref: TAPESPECIFICATION, hint: "The tape running across it" },
        { key: "ends_per_inch", label: "Ends per inch", kind: "decimal", places: 2, hint: "Warp tapes per inch, measured around the tube" },
        { key: "picks_per_inch", label: "Picks per inch", kind: "decimal", places: 2, hint: "Weft tapes per inch, along it" },
        { key: "lay_flat_width_cm", label: "Lay flat width cm", kind: "decimal", places: 2, hint: "The width of the tube laid flat, which is the width of the sack it will become" },
        { key: "shrink_percent", label: "Shrink %", kind: "decimal", places: 2, initial: "4", hint: "How much finer the tape line runs than the fabric weighs" },
        { key: "weave", label: "Weave", kind: "choice", choices: [["tubular", "Tubular (circular loom)"], ["flat", "Flat"]], initial: "tubular" },
        { key: "is_leno", label: "Leno", kind: "bool", initial: false, hint: "Open mesh, the warp tapes twisted in pairs around the weft" },
        { key: "target_gsm", label: "Target GSM", kind: "decimal", places: 2, hint: "What the customer was quoted" },
        { key: "gsm_tolerance_percent", label: "GSM tolerance %", kind: "decimal", places: 2, initial: "5", hint: "How far the mesh may put the fabric from the target before the specification is wrong…" },
        { key: "weaving_waste_percent", label: "Weaving waste %", kind: "decimal", places: 3, initial: "2", hint: "Of the tape fed in" },
        { key: "waste_recovered_percent", label: "Waste recovered %", kind: "decimal", places: 3, initial: "85", hint: "How much of that loss is collected and reground" },
        { key: "loom_waste_item", label: "Loom waste item", kind: "pick", pick: ITEM, hint: "What the collected loom waste is booked as — usually the same regrind the extruder…" },
        { key: "bom", label: "Recipe it writes", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "gsm", label: "Gsm", readOnly: true },
        { key: "gsm_deviation_percent", label: "Gsm deviation percent", readOnly: true },
        { key: "grams_per_metre", label: "Grams per metre", readOnly: true },
        { key: "metres_per_kg", label: "Metres per kg", readOnly: true },
        { key: "warp_strength_min_n", label: "Warp strength min N", kind: "decimal", places: 2, hint: "Least tensile strength along the warp, newtons a 5 cm strip" },
        { key: "weft_strength_min_n", label: "Weft strength min N", kind: "decimal", places: 2, hint: "Across the weft" },
        { key: "mesh_tolerance_per_inch", label: "Mesh tolerance per inch", kind: "decimal", places: 2, hint: "Given, the lab counts ends and picks an inch against the mesh, give or take this many" },
        { key: "valid_from", label: "From", kind: "date", hint: "The first date output made to this specification is due" },
        { key: "valid_to", label: "To", kind: "date", hint: "The last date output made to this specification is due" },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING, hint: "The machines this passes through" },
      ]}
    />
  );
}
