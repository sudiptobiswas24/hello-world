import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "section_code", label: "Section", width: "7rem" },
  { key: "applies_from", label: "From", kind: "date", width: "8rem" },
  { key: "rate_percent", label: "Rate %", width: "6rem" },
  { key: "no_pan_rate_percent", label: "Without PAN %", width: "8rem" },
  { key: "authority", label: "Authority" },
  { key: "confirmed_on", label: "", width: "9rem", render: (row) => (row.confirmed_on ? "Confirmed" : "To confirm") },
];

/** TDS rates as the law sets them, by the day each applies from; deducted at only once the accountant confirms. */
export default function TdsRates() {
  return (
    <ListView<Row>
      title="TDS rates"
      noun={["rate", "rates"]}
      endpoint="/api/accounting/tds-rates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/tds-rates/${row.id}`}
      create={{ href: "/settings/tds-rates/new", permission: "accounting.add_tdsrate" }}
    />
  );
}
