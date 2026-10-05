import { ListView, type Column } from "../../views/ListView";

interface Party {
  id: number;
  code: string;
  name: string;
  email: string;
  phone: string;
  tax_id: string;
  is_active: boolean;
}

const columns: Column<Party>[] = [
  { key: "code", label: "Code", sort: "code", width: "8rem" },
  { key: "name", label: "Name", sort: "name" },
  { key: "tax_id", label: "GSTIN", width: "12rem" },
  { key: "phone", label: "Phone", width: "10rem" },
  { key: "email", label: "Email" },
  {
    key: "is_active",
    label: "State",
    width: "6rem",
    render: (row) => <span className={`pill pill-${row.is_active ? "done" : "draft"}`}>{row.is_active ? "Active" : "Archived"}</span>,
  },
];

const FIXED = { role_assignments__role: "customer" };

export default function Customers() {
  return (
    <ListView<Party>
      title="Customers"
      noun={["customer", "customers"]}
      endpoint="/api/core/parties/"
      fixed={FIXED}
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/customers/${row.id}`}
      create={{ href: "/sales/customers/new", permission: "core.add_party" }}
      searchHint="Code, name, GSTIN, phone, email"
      facets={[
        { label: "Active", params: { is_active: "true" } },
        { label: "Archived", params: { is_active: "false" } },
      ]}
    />
  );
}
