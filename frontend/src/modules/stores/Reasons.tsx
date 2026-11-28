import { ListView, type Column } from "../../views/ListView";

interface Reason { id: number; code: string; name: string; direction: string; account_label: string; is_active: boolean; [key: string]: unknown }

const columns: Column<Reason>[] = [
  { key: "code", label: "Code", width: "9rem", sort: "code" },
  { key: "name", label: "Reason" },
  { key: "direction", label: "Way", width: "8rem", kind: "status" },
  { key: "account_label", label: "Account" },
];

/** Why stock is written on or off, and which account takes the value. */
export default function Reasons() {
  return (
    <ListView<Reason>
      title="Adjustment reasons"
      noun={["reason", "reasons"]}
      endpoint="/api/inventory/adjustment-reasons/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/reasons/${row.id}`}
      searchHint="Code, reason"
      create={{ href: "/stores/reasons/new", permission: "inventory.add_adjustmentreason" }}
    />
  );
}
