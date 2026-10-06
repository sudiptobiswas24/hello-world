import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "name", label: "Name" },
  { key: "description", label: "Description" },
];

export default function PartyTags() {
  return (
    <ListView<Row>
      title="Party tags"
      noun={["party tag", "party tags"]}
      endpoint="/api/core/party-tags/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/party-tags/${row.id}`}
      searchHint="Name"
      create={{ href: "/settings/party-tags/new", permission: "core.add_partytag" }}
    />
  );
}
