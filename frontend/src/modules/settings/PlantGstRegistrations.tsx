import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "gstin", label: "GSTIN" },
  { key: "state_name", label: "State" },
  { key: "einvoicing_from", label: "E-invoicing from", kind: "date", width: "9rem" },
  { key: "is_active", label: "In force", width: "6rem", render: (row) => (row.is_active ? "Yes" : "No") },
];

/**
 * The plant's own GST registration: its GSTIN, the state read off it, what a supply across a state line maps its
 * taxes through, and the limits set by notification. Not the parties' registrations, which are on their own screen.
 */
export default function PlantGstRegistrations() {
  return (
    <ListView<Row>
      title="Plant's GST registration"
      noun={["registration", "registrations"]}
      endpoint="/api/accounting/gst-settings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/plant-gst/${row.id}`}
      emptyHint="None yet: no invoice carries GST and no return compiles until the plant's GSTIN is entered here."
      create={{ href: "/settings/plant-gst/new", permission: "accounting.add_gstsettings" }}
    />
  );
}
