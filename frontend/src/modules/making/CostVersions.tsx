import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Version { id: number; code: string; name: string; effective_from: string; published_on: string | null;
  items: number; [key: string]: unknown }

const columns: Column<Version>[] = [
  { key: "code", label: "Version", sort: "code", width: "9rem" },
  { key: "name", label: "Name" },
  { key: "effective_from", label: "From", sort: "effective_from", width: "8rem", render: (row) => date(row.effective_from) },
  { key: "items", label: "Items", width: "6rem", render: (row) => String(row.items) },
  { key: "published_on", label: "", width: "10rem", render: (row) => (row.published_on ? `Published ${date(row.published_on)}` : "Draft") },
];

export default function CostVersions() {
  return (
    <ListView<Version>
      title="Standard cost versions"
      noun={["cost version", "cost versions"]}
      endpoint="/api/manufacturing/cost-versions/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/cost-versions/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/cost-versions/new", permission: "manufacturing.add_costversion" }}
    />
  );
}
