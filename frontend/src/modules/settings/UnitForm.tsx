import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const UNITOFMEASURE: FieldDef["ref"] = { endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure", label: (row: Row) => String(row.code) };

/** A unit things are counted or weighed in, and how many of its base unit it holds. */
export default function UnitForm() {
  return (
    <RecordScreen
      endpoint="/api/core/units-of-measure/"
      back="/settings/units"
      backLabel="Units of measure"
      newTitle="New unit of measure"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "core.add_unitofmeasure", change: "core.change_unitofmeasure", delete: "core.delete_unitofmeasure" }}
      fields={[
        { key: "code", label: "Code", hint: "e.g. pcs, kg, L" },
        { key: "name", label: "Name" },
        { key: "category", label: "Category", kind: "choice", choices: [["count", "Count"], ["weight", "Weight"], ["volume", "Volume"], ["length", "Length"], ["time", "Time"], ["other", "Other"]], initial: "count" },
        { key: "base_unit", label: "Base unit", kind: "ref", ref: UNITOFMEASURE, hint: "Leave blank if this unit IS a base unit (e.g. 'each', 'kg')" },
        { key: "conversion_factor", label: "Conversion factor", kind: "decimal", places: 6, initial: "1", hint: "Quantity in this unit * conversion_factor = equivalent quantity in base_unit" },
      ]}
    />
  );
}
