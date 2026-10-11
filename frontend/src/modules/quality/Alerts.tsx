import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", sort: "number", width: "9rem" },
  { key: "raised_on", label: "Raised", kind: "date", sort: "raised_on", width: "8rem" },
  { key: "title", label: "What was found" },
  { key: "where", label: "Where", width: "8rem" },
  { key: "severity", label: "How bad", kind: "status", width: "7rem" },
  { key: "owner_name", label: "With" },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

/** What the plant found wrong before a customer did, and who is dealing with it. */
export default function Alerts() {
  return (
    <ListView<Row>
      title="Quality alerts"
      noun={["alert", "alerts"]}
      endpoint="/api/manufacturing/quality-alerts/"
      columns={columns}
      rowHref={(row) => `/quality/alerts/${row.id}`}
      searchHint="Number or what was found"
      facets={[{ label: "Open", params: { status: "open" } }, { label: "High", params: { severity: "high", status: "open" } }]}
      create={{ href: "/quality/alerts/new", permission: "manufacturing.add_qualityalert" }}
    />
  );
}
