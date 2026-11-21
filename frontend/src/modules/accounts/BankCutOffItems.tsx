import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const state = (row: Row) => (row.cancelled_by ? `Cancelled, JE-${String(row.cancelled_by)}`
  : row.shown_on ? `Shown on ${String(row.shown_on)}` : "Outstanding");

const columns: Column<Row>[] = [
  { key: "bank_account_name", label: "Bank account" },
  { key: "date", label: "Written", kind: "date", width: "8rem" },
  { key: "reference", label: "Reference", width: "10rem" },
  { key: "description", label: "Description" },
  { key: "amount", label: "Amount", kind: "money", width: "10rem" },
  { key: "shown_on", label: "", width: "14rem", render: state },
];

/**
 * What was outstanding at each bank's cut-off, the day its statements are
 * reconciled from (O615): the cheques and entries the bank had not shown by
 * then. Everything else dated before that day cleared by then.
 */
export default function BankCutOffItems() {
  return (
    <ListView<Row>
      title="Outstanding at a bank's cut-off"
      noun={["item", "items"]}
      endpoint="/api/accounting/bank-cut-off-items/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/bank-cut-off/${row.id}`}
      searchHint="Reference, description or bank account"
      facets={[{ label: "Not shown yet", params: { line__isnull: "true", cancelled_by__isnull: "true" } },
        { label: "Shown", params: { line__isnull: "false" } }]}
      create={{ href: "/accounts/bank-cut-off/new", permission: "accounting.add_bankcutoffitem" }}
    />
  );
}
