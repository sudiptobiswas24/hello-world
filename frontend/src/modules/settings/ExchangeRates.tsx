import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "currency_code", label: "Currency" },
  { key: "rate", label: "Rate", kind: "quantity" },
  { key: "valid_from", label: "From", kind: "date" },
];

export default function ExchangeRates() {
  return (
    <ListView<Row>
      title="Exchange rates"
      noun={["exchange rate", "exchange rates"]}
      endpoint="/api/core/exchange-rates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/exchange-rates/${row.id}`}
      searchHint="Currency"
      create={{ href: "/settings/exchange-rates/new", permission: "core.add_exchangerate" }}
    />
  );
}
