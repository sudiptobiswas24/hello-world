import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "No.", sort: "created_at", width: "9rem" },
  { key: "company_name", label: "Who asked", sort: "company_name" },
  { key: "contact_name", label: "Contact", width: "11rem" },
  { key: "city", label: "City", width: "8rem" },
  { key: "source", label: "From", kind: "status", width: "8rem" },
  { key: "owner_name", label: "Rep", width: "10rem" },
  { key: "status", label: "", kind: "status", width: "8rem" },
];

/** Enquiries that may become customers: a rep's own, and the ones nobody has taken yet. */
export default function Leads() {
  return (
    <ListView<Row>
      title="Leads"
      noun={["lead", "leads"]}
      endpoint="/api/sales/leads/"
      columns={columns}
      facets={[
        { label: "New", params: { status: "new" } },
        { label: "Being worked", params: { status: "working" } },
        { label: "Converted", params: { status: "converted" } },
        { label: "Lost", params: { status: "lost" } },
      ]}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/leads/${row.id}`}
      searchHint="Company, contact, phone or city"
      create={{ href: "/sales/leads/new", permission: "sales.add_lead" }}
    />
  );
}
