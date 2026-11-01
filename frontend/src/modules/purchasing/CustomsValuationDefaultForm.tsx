import { RecordScreen } from "../../views/RecordScreen";

/** One dated pair of freight and insurance percentages, and whether the customs broker has confirmed it. */
export default function CustomsValuationDefaultForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/customs-valuation-defaults/"
      back="/purchasing/customs-valuation"
      backLabel="Customs valuation defaults"
      newTitle="New customs valuation default"
      heading={(row) => `From ${String(row.valid_from ?? "")}`}
      permissions={{ add: "purchasing.add_customsvaluationdefault", change: "purchasing.change_customsvaluationdefault" }}
      fields={[
        { key: "valid_from", label: "From", kind: "date", createOnly: true },
        { key: "freight_percent", label: "Freight, % of FOB", kind: "decimal" },
        { key: "insurance_percent", label: "Insurance, % of FOB", kind: "decimal" },
        { key: "citation", label: "Rule" },
        { key: "confirmed", label: "Confirmed by the customs broker or the accountant", kind: "bool" },
      ]}
    />
  );
}
