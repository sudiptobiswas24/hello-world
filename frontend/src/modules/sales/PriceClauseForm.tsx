import { RecordScreen } from "../../views/RecordScreen";
import { INDEX, ORDER_LINE } from "./extraRefs";

/**
 * An order line's price moving with a polymer index: each delivery is
 * billed the change from the base value, on the polymer a unit holds,
 * once the move passes the threshold.
 */
export default function PriceClauseForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/price-clauses/"
      back="/sales/price-clauses"
      backLabel="Price variation clauses"
      newTitle="New price variation clause"
      heading={(row) => String(row.line_label ?? "")}
      permissions={{ add: "sales.add_pricevariationclause", change: "sales.change_pricevariationclause",
        delete: "sales.delete_pricevariationclause" }}
      fields={[
        { key: "order_line", label: "Order line", kind: "pick", pick: ORDER_LINE(), createOnly: true,
          show: (row) => String(row.line_label ?? "") },
        { key: "index", label: "Index", kind: "ref", ref: INDEX },
        { key: "base_value", label: "Base value", kind: "decimal", hint: "The index value the price was agreed at" },
        { key: "polymer_kg_per_unit", label: "Polymer kg a unit", kind: "decimal", places: 6, hint: "What one unit of the line holds" },
        { key: "pass_through_percent", label: "Passed on %", kind: "decimal", places: 2, initial: "100" },
        { key: "threshold_percent", label: "Threshold %", kind: "decimal", places: 2, initial: "0",
          hint: "A move smaller than this is not billed" },
      ]}
    />
  );
}
