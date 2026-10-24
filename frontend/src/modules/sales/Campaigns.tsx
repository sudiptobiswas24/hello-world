import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code", sort: "code", width: "9rem" },
  { key: "name", label: "Campaign" },
  { key: "channel", label: "", kind: "status", width: "8rem" },
  { key: "starts_on", label: "From", kind: "date", sort: "starts_on", width: "8rem" },
  { key: "ends_on", label: "To", kind: "date", width: "8rem" },
  { key: "budget", label: "Budget", kind: "money", width: "10rem" },
];

/** What brought the leads in, and what each brought back. */
export default function Campaigns() {
  return (
    <ListView<Row>
      title="Campaigns"
      noun={["campaign", "campaigns"]}
      endpoint="/api/sales/campaigns/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/campaigns/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/sales/campaigns/new", permission: "sales.add_campaign" }}
    />
  );
}
