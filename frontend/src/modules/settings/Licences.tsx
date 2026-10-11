import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "kind_label", label: "Licence" },
  { key: "licence_number", label: "Number", sort: "licence_number" },
  { key: "covers", label: "For" },
  { key: "valid_to", label: "Valid to", kind: "date", sort: "valid_to" },
  { key: "renew_from", label: "Renew from", kind: "date" },
  { key: "status", label: "", kind: "status" },
];

/** The plant's licences, soonest to lapse first; the ones not yet renewed are the calendar. */
export default function Licences() {
  return (
    <ListView<Row>
      title="Licences"
      noun={["licence", "licences"]}
      endpoint="/api/core/licences/"
      columns={columns}
      facets={[{ label: "Not renewed", params: { renewed_by__isnull: "true" } }]}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/licences/${row.id}`}
      searchHint="Number, office or what it is for"
      create={{ href: "/settings/licences/new", permission: "core.add_licence" }}
    />
  );
}
