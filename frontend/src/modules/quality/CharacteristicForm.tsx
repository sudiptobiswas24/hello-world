import { RecordScreen } from "../../views/RecordScreen";

export default function CharacteristicForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/characteristics/"
      back="/quality/characteristics"
      backLabel="Characteristics"
      newTitle="New characteristic"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      permissions={{ add: "quality.add_characteristic", change: "quality.change_characteristic" }}
      fields={[
        { key: "code", label: "Code", createOnly: true, hint: "TENACITY, GSM, MFI" },
        { key: "name", label: "What is tested" },
        { key: "kind", label: "Kind", kind: "choice", choices: [["measured", "Measured against limits"], ["attribute", "Present or absent"]], createOnly: true },
        { key: "uom", label: "Unit", kind: "ref", ref: { endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure",
          label: (row) => `${String(row.code)} · ${String(row.name)}` } },
        { key: "decimal_places", label: "Places", kind: "integer", initial: "2" },
        { key: "needs_calibrated_instrument", label: "Needs a calibrated instrument", kind: "bool" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
