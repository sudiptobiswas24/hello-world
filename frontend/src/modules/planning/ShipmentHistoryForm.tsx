import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, WAREHOUSE } from "../stores/refs";

/** One month the old system shipped an item from a warehouse: a month that has ended and that this system did not ship in itself. */
export default function ShipmentHistoryForm() {
  return (
    <RecordScreen
      endpoint="/api/planning/shipment-history/"
      back="/planning/shipment-history"
      backLabel="Shipment history"
      newTitle="A month shipped"
      heading={(row) => `${String(row.item_sku ?? "")} · ${String(row.month ?? "")}`}
      permissions={{ add: "planning.add_shipmenthistory", change: "planning.change_shipmenthistory", delete: "planning.delete_shipmenthistory" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM, createOnly: true, show: (row) => `${String(row.item_sku ?? "")} · ${String(row.item_name ?? "")}` },
        { key: "warehouse", label: "From", kind: "ref", ref: WAREHOUSE, createOnly: true },
        { key: "month", label: "Month", kind: "date", createOnly: true, hint: "Its first day" },
        { key: "quantity", label: "Shipped, net of returns", kind: "decimal", places: 4, hint: "In the item's stock unit" },
        { key: "note", label: "Source", hint: "The old system's register, a ledger" },
      ]}
    />
  );
}
