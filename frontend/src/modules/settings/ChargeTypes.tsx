import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "hsn_code", label: "SAC" },
  { key: "capitalise_into_inventory", label: "Into stock value" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ChargeTypes() {
  return (
    <ListView<Row>
      title="Charge types"
      noun={["charge type", "charge types"]}
      endpoint="/api/accounting/charge-types/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/charge-types/${row.id}`}
      searchHint="Code, name or SAC"
      create={{ href: "/settings/charge-types/new", permission: "accounting.add_chargetype" }}
    />
  );
}
