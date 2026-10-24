import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "label", label: "Seeded" },
  { key: "model", label: "Kind", width: "14rem" },
  { key: "reviewed_on", label: "Reviewed", width: "14rem",
    render: (row) => (row.reviewed_on ? `${String(row.reviewed_on)} by ${String(row.reviewed_by_name ?? "")}` : "To review") },
];

/** What setting up the Indian books made for the accountant to review: accounts, taxes, positions, rates. */
export default function SeededDefaults() {
  return (
    <ListView<Row>
      title="Seeded defaults"
      noun={["default", "defaults"]}
      endpoint="/api/accounting/seeded-defaults/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/seeded-defaults/${row.id}`}
    />
  );
}
