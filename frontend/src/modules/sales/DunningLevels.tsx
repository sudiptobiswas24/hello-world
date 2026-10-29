import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "name", label: "Name" },
  { key: "days_overdue", label: "Days overdue", kind: "quantity" },
  { key: "subject", label: "Subject" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function DunningLevels() {
  return (
    <ListView<Row>
      title="Reminder levels"
      noun={["reminder level", "reminder levels"]}
      endpoint="/api/sales/dunning-levels/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/dunning-levels/${row.id}`}
      searchHint="Name"
      create={{ href: "/sales/dunning-levels/new", permission: "sales.add_dunninglevel" }}
    />
  );
}
