import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "gstin", label: "GSTIN" },
  { key: "gst_state", label: "GST state" },
  { key: "gst_registration", label: "GST registration" },
  { key: "tax_exempt", label: "Tax exempt" },
];

export default function GstRegistrations() {
  return (
    <ListView<Row>
      title="GST registrations"
      noun={["GST registration", "GST registrations"]}
      endpoint="/api/accounting/party-tax-profiles/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/gst-registrations/${row.id}`}
      searchHint="GSTIN or party"
      create={{ href: "/settings/gst-registrations/new", permission: "accounting.add_partytaxprofile" }}
    />
  );
}
