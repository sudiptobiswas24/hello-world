import { dateTime } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "E-way bill", width: "11rem", render: (row) => String(row.number || "Not generated yet") },
  { key: "vehicle_number", label: "Vehicle", width: "9rem" },
  { key: "transporter_name", label: "Transporter" },
  { key: "distance_km", label: "Km", kind: "quantity", width: "6rem" },
  { key: "valid_until", label: "Valid until", width: "11rem", render: (row) => (row.valid_until ? dateTime(String(row.valid_until)) : "") },
  { key: "cancelled_at", label: "", width: "7rem", render: (row) => (row.cancelled_at ? "Cancelled" : "") },
];

export default function EwayBills() {
  return (
    <ListView<Row>
      title="E-way bills"
      noun={["e-way bill", "e-way bills"]}
      endpoint="/api/gst/eway-bills/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/eway-bills/${row.id}`}
      searchHint="Number or vehicle"
      create={{ href: "/accounts/eway-bills/new", permission: "gst.add_ewaybill" }}
    />
  );
}
