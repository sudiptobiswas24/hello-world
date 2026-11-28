import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "applies_from", label: "From", kind: "date", width: "8rem" },
  { key: "rate_percent", label: "Bank rate %", width: "8rem" },
  { key: "authority", label: "Notification" },
  { key: "confirmed_on", label: "", width: "9rem", render: (row) => (row.confirmed_on ? "Confirmed" : "To confirm") },
];

/**
 * The Reserve Bank's bank rate by the day each applies from. Three times it is the interest a micro or small
 * supplier paid late is owed (MSMED Act s.16); none is worked until the accountant confirms the rate.
 */
export default function BankRates() {
  return (
    <ListView<Row>
      title="Bank rates"
      noun={["rate", "rates"]}
      endpoint="/api/accounting/bank-rates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/bank-rates/${row.id}`}
      create={{ href: "/settings/bank-rates/new", permission: "accounting.add_bankrate" }}
    />
  );
}
