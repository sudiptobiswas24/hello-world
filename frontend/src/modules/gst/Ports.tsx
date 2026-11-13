import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "label", label: "Port or depot", width: "14rem" },
  { key: "line1", label: "Address" },
  { key: "city", label: "Town", width: "11rem" },
  { key: "state", label: "State", width: "10rem" },
  { key: "postal_code", label: "PIN", width: "6rem" },
];

/** Ports, ICDs and CFSs an export leaves the plant for: addresses that are nobody's party. */
export default function Ports() {
  return (
    <ListView<Row>
      title="Ports and depots"
      noun={["port or depot", "ports and depots"]}
      endpoint="/api/core/addresses/"
      fixed={{ party__isnull: "true", address_type: "other" }}
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/ports/${row.id}`}
      searchHint="Name, town or PIN"
      create={{ href: "/accounts/ports/new", permission: "core.add_address" }}
    />
  );
}
