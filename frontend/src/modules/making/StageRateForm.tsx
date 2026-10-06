import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const WORKCENTRE: FieldDef["ref"] = { endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** What a stage of making costs a kilogramme in a quotation, from a date. */
export default function StageRateForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/stage-rates/"
      back="/making/stage-rates"
      backLabel="Conversion rates"
      newTitle="New conversion rate"
      heading={(row) => String(row.stage ?? "") + " · " + String(row.valid_from ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_stagerate", change: "manufacturing.change_stagerate", delete: "manufacturing.delete_stagerate" }}
      fields={[
        { key: "stage", label: "Stage", kind: "choice", choices: [["tape", "Tape extrusion"], ["weaving", "Weaving"], ["lamination", "Lamination"], ["printing", "Printing"], ["cutting", "Cutting and stitching"], ["blown_film", "Liner film blowing"], ["valve", "Valve fixing"], ["dcut", "D-cut punching"], ["handle", "Handle attachment"], ["liner", "Liner insertion"], ["packing", "Bale packing"], ["sealing", "Liner cutting and sealing"]] },
        { key: "rate", label: "Rate", kind: "decimal", hint: "Per kilogramme the stage makes, or per sack for the per-sack operations (valve, D-cut,…" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "note", label: "Note" },
        { key: "work_centre", label: "Work centre", kind: "ref", ref: WORKCENTRE, hint: "The centre that does this stage" },
      ]}
    />
  );
}
