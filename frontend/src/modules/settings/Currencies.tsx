import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "symbol", label: "Symbol" },
  { key: "decimal_places", label: "Places", kind: "quantity" },
];

export default function Currencies() {
  return (
    <ListView<Row>
      title="Currencies"
      noun={["currency", "currencies"]}
      endpoint="/api/core/currencies/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/currencies/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/currencies/new", permission: "core.add_currency" }}
    />
  );
}
