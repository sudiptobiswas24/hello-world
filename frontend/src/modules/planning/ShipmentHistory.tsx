import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "month", label: "Month", kind: "date", sort: "month", width: "9rem" },
  { key: "item_sku", label: "Item", sort: "item__sku", width: "10rem" },
  { key: "item_name", label: "" },
  { key: "warehouse_code", label: "From", width: "7rem" },
  { key: "quantity", label: "Shipped, net", kind: "quantity", width: "10rem" },
  { key: "note", label: "Source" },
];

/**
 * What the old system shipped, a month at a time, so the seasonal
 * forecast has a past to read before a year of deliveries has gone
 * through here. Brought in with `import_csv shipment_history`, or typed.
 */
export default function ShipmentHistory() {
  return (
    <ListView<Row>
      title="Shipment history"
      noun={["month", "months"]}
      endpoint="/api/planning/shipment-history/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/planning/shipment-history/${row.id}`}
      searchHint="Item or source"
      create={{ href: "/planning/shipment-history/new", permission: "planning.add_shipmenthistory" }}
    />
  );
}
