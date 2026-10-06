import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const FABRICSPECIFICATION: FieldDef["ref"] = { endpoint: "/api/manufacturing/fabric-specifications/", permission: "manufacturing.view_fabricspecification", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const PRINTDESIGN: FieldDef["ref"] = { endpoint: "/api/manufacturing/print-designs/", permission: "manufacturing.view_printdesign", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ROUTING: FieldDef["ref"] = { endpoint: "/api/manufacturing/routings/", permission: "manufacturing.view_routing", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** A sack as the customer orders it: its fabric, size, print, lamination, stitching and the tests it must pass. Saved, it writes the recipe conversion runs. */
export default function BagSpecForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/bag-specifications/"
      back="/making/bags"
      backLabel="Bag specifications"
      newTitle="New bag specification"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_bagspecification", change: "manufacturing.change_bagspecification", delete: "manufacturing.delete_bagspecification" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "bag_item", label: "Bag item", kind: "pick", pick: ITEM, hint: "The finished sack, counted in pieces" },
        { key: "fabric", label: "Fabric", kind: "ref", ref: FABRICSPECIFICATION },
        { key: "bag_width_cm", label: "Bag width cm", kind: "decimal", places: 2, hint: "The finished sack's width, which is the fabric's lay-flat width" },
        { key: "bag_length_cm", label: "Bag length cm", kind: "decimal", places: 2, hint: "The finished sack's length, hems excluded" },
        { key: "bottom_hem_cm", label: "Bottom hem cm", kind: "decimal", places: 2, initial: "3", hint: "Fabric turned up and stitched at the bottom" },
        { key: "top_hem_cm", label: "Top hem cm", kind: "decimal", places: 2, initial: "2", hint: "Fabric folded and hemmed at the mouth, one stitch row" },
        { key: "is_laminated", label: "Laminated", kind: "bool", initial: false, readOnly: true, hint: "Laminated while it has a coating blend: add the first polymer below" },
        { key: "lamination_gsm", label: "Lamination GSM", kind: "decimal", places: 2, initial: "0", hint: "Weight of the coating per square metre of fabric, typically 12 to 20" },
        { key: "lamination_waste_percent", label: "Lamination waste %", kind: "decimal", places: 3, initial: "4", hint: "Of the coating polymer fed in" },
        { key: "print_colours", label: "Print colours", kind: "integer", initial: 0, hint: "Colours printed on the front face" },
        { key: "print_colours_back", label: "Print colours back", kind: "integer", initial: 0, hint: "Colours printed on the back face" },
        { key: "ink_grams_per_sqm_per_colour", label: "Ink grams per sqm per colour", kind: "decimal", places: 3, initial: "0.5", hint: "Ink laid down per square metre per colour" },
        { key: "ink_item", label: "Ink item", kind: "pick", pick: ITEM },
        { key: "reducer_item", label: "Reducer item", kind: "pick", pick: ITEM, hint: "Thins the ink on the press and evaporates" },
        { key: "reducer_percent", label: "Reducer %", kind: "decimal", places: 2, initial: "0", hint: "Of the ink's weight" },
        { key: "solvent_item", label: "Solvent item", kind: "pick", pick: ITEM, hint: "MIBK or another press solvent" },
        { key: "solvent_percent", label: "Solvent %", kind: "decimal", places: 2, initial: "0", hint: "Of the ink's weight" },
        { key: "fold_type", label: "Fold type", kind: "choice", choices: [["", "Not stated"], ["SFSS", "Single fold, single stitch"], ["SFDS", "Single fold, double stitch"], ["DFSS", "Double fold, single stitch"], ["DFDS", "Double fold, double stitch"], ["EZWF", "Easy-open with fold"], ["EZWOF", "Easy-open without fold"]], hint: "How the bottom is folded and stitched" },
        { key: "thread_grams_per_bag", label: "Thread grams per bag", kind: "decimal", places: 3, initial: "0", hint: "Sewing thread, typed" },
        { key: "thread_denier", label: "Thread denier", kind: "decimal", places: 2, initial: "0", hint: "The sewing yarn's denier, to compute the thread from the seams" },
        { key: "stitches_per_dm", label: "Stitches per dm", kind: "decimal", places: 2, initial: "12.5", hint: "Stitch density" },
        { key: "thread_item", label: "Thread item", kind: "pick", pick: ITEM },
        { key: "liner_item", label: "Liner item", kind: "pick", pick: ITEM, hint: "An inner LDPE liner, for sacks that must keep moisture out" },
        { key: "print_design", label: "Print design", kind: "ref", ref: PRINTDESIGN, hint: "The artwork it is printed with" },
        { key: "registration_tolerance_mm", label: "Registration tolerance mm", kind: "decimal", places: 2, initial: "1.00", hint: "Printed" },
        { key: "max_delta_e", label: "Max delta E", kind: "decimal", places: 2, initial: "2.00", hint: "Printed" },
        { key: "seconds_item", label: "Seconds item", kind: "pick", pick: ITEM, hint: "What an off-grade sack is sold as" },
        { key: "seconds_percent", label: "Seconds %", kind: "decimal", places: 3, initial: "0", hint: "Of what is fed into conversion, the share that comes out a second rather than a first…" },
        { key: "seam_strength_min_n", label: "Seam strength min N", kind: "decimal", places: 2, hint: "Least load the bottom seam holds, newtons, on the mean of five" },
        { key: "drop_test_drops", label: "Drop test drops", kind: "integer", hint: "Drops a filled sack must survive" },
        { key: "bond_strength_min_n", label: "Bond strength min N", kind: "decimal", places: 2, hint: "Laminated sacks" },
        { key: "uv_retention_min_percent", label: "UV retention min %", kind: "decimal", places: 2, hint: "Least share of its strength the fabric keeps after UV exposure" },
        { key: "uv_exposure_hours", label: "UV exposure hours", kind: "integer", hint: "The hours of exposure that share is after" },
        { key: "liner_grams_per_bag", label: "Liner grams per bag", kind: "decimal", places: 3, initial: "0", hint: "Typed, where the liner is bought or weighed as a finished piece" },
        { key: "conversion_waste_percent", label: "Conversion waste %", kind: "decimal", places: 3, initial: "2.5", hint: "Of the fabric fed into cutting and stitching" },
        { key: "waste_recovered_percent", label: "Waste recovered %", kind: "decimal", places: 3, initial: "70", hint: "How much of that offcut is collected and reground" },
        { key: "cutting_waste_item", label: "Cutting waste item", kind: "pick", pick: ITEM },
        { key: "bom", label: "Recipe it writes", readOnly: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "gusset_cm", label: "Gusset cm", kind: "decimal", places: 2, initial: "0", hint: "How deep each side's gusset folds in" },
        { key: "closure", label: "Closure", kind: "choice", choices: [["sewn", "Sewn"], ["welded", "Welded (block bottom)"]], initial: "sewn", hint: "Sewn with thread, or folded into a block bottom and welded with hot air, as valve…" },
        { key: "bopp_film_item", label: "BOPP film item", kind: "pick", pick: ITEM, hint: "Printed BOPP film laminated over the fabric" },
        { key: "bopp_micron", label: "BOPP micron", kind: "decimal", places: 2, initial: "0", hint: "Film thickness" },
        { key: "bopp_faces", label: "BOPP faces", kind: "choice", choices: [["1", "One face (SS)"], ["2", "Both faces (BS)"]], initial: 2, hint: "Film laminated on one face of the flattened tube or on both" },
        { key: "bopp_waste_percent", label: "BOPP waste %", kind: "decimal", places: 3, initial: "4", hint: "Of the film fed in" },
        { key: "valve_patch_item", label: "Valve patch item", kind: "pick", pick: ITEM, hint: "The valve a valve sack is filled through" },
        { key: "valve_patch_grams", label: "Valve patch grams", kind: "decimal", places: 3, initial: "0" },
        { key: "cover_patch_item", label: "Cover patch item", kind: "pick", pick: ITEM, hint: "The cover sheets welded over a block bottom's folds" },
        { key: "cover_patch_grams", label: "Cover patch grams", kind: "decimal", places: 3, initial: "0", hint: "Both ends together" },
        { key: "handle_item", label: "Handle item", kind: "pick", pick: ITEM, hint: "A handle strip or loop sewn to the sack" },
        { key: "handle_grams", label: "Handle grams", kind: "decimal", places: 3, initial: "0" },
        { key: "dcut_area_sqcm", label: "Dcut area sqcm", kind: "decimal", places: 2, initial: "0", hint: "The D-cut handle's hole, on one face" },
        { key: "metallic_film_item", label: "Metallic film item", kind: "pick", pick: ITEM, hint: "Metallised film on the front face, bonded by the coating" },
        { key: "metallic_micron", label: "Metallic micron", kind: "decimal", places: 2, initial: "0" },
        { key: "metallic_coverage_percent", label: "Metallic coverage %", kind: "decimal", places: 2, initial: "100", hint: "Of the face the film covers" },
        { key: "liner_micron", label: "Liner micron", kind: "decimal", places: 2, initial: "0", hint: "Liner film thickness, to compute its weight from its size" },
        { key: "liner_width_cm", label: "Liner width cm", kind: "decimal", places: 2, initial: "0" },
        { key: "liner_length_cm", label: "Liner length cm", kind: "decimal", places: 2, initial: "0" },
        { key: "target_grams", label: "Target grams", kind: "decimal", places: 3, hint: "The weight the customer contracted for" },
        { key: "weight_tolerance_percent", label: "Weight tolerance %", kind: "decimal", places: 2, initial: "5", hint: "How far a finished sack may be from its contracted weight, or its computed one when…" },
        { key: "cut_length_cm", label: "Cut length cm", readOnly: true },
        { key: "fabric_area_sqm", label: "Fabric area sqm", readOnly: true },
        { key: "fabric_grams", label: "Fabric grams", readOnly: true },
        { key: "bag_grams", label: "Bag grams", readOnly: true },
        { key: "fabric_metres_per_bag", label: "Fabric metres per bag", readOnly: true },
        { key: "construction", label: "Construction", readOnly: true },
        { key: "valid_from", label: "From", kind: "date", hint: "The first date output made to this specification is due" },
        { key: "valid_to", label: "To", kind: "date", hint: "The last date output made to this specification is due" },
        { key: "lamination_tolerance_percent", label: "Lamination tolerance %", kind: "decimal", places: 2, initial: "10", hint: "How far the coating weighed off the coater may sit from its GSM" },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING, hint: "The machines this passes through" },
      ]}
      panels={[{
        title: "Coating blend", permission: "manufacturing.view_bagspecification", endpoint: "", query: () => ({}),
        rows: (record) => ((record.coating as Row[]) ?? []).map((line) => ({ ...line, id: Number(line.item), spec: record.id })),
        columns: [
          { key: "item_label", label: "Polymer" },
          { key: "parts", label: "Parts", kind: "quantity", width: "8rem" },
        ],
        adder: {
          label: "Add a polymer", permission: "manufacturing.change_bagspecification",
          url: (record) => `/api/manufacturing/bag-specifications/${String(record.id)}/coating/`,
          fields: (record) => [
            { key: "item", label: "Polymer", kind: "pick", pick: ITEM },
            { key: "parts", label: "Parts", kind: "decimal", places: 3, hint: "Its share by weight: 80 and 20, or 4 and 1" },
            ...(record.is_laminated ? [] : [{ key: "lamination_gsm", label: "Coating GSM", kind: "decimal", places: 2, hint: "The sack becomes laminated: the coating's weight per square metre, typically 12 to 20" } as FieldDef]),
          ],
          body: (values) => values,
        },
        remover: {
          permission: "manufacturing.change_bagspecification",
          url: (row) => `/api/manufacturing/bag-specifications/${String(row.spec)}/coating/?item=${String(row.item)}`,
        },
      }]}
    />
  );
}
