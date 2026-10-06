import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Complaint { id: number; number: string; received_on: string; customer_name: string; category: string; status: string; [key: string]: unknown }

const columns: Column<Complaint>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "received_on", label: "Received", width: "8rem", sort: "received_on", render: (row) => date(row.received_on) },
  { key: "customer_name", label: "Customer" },
  { key: "category", label: "About", width: "10rem", kind: "status" },
  { key: "status", label: "State", width: "7rem", kind: "status" },
];

/** What customers said was wrong, the batches it came from, and what was done so it does not happen again. */
export default function Complaints() {
  return (
    <ListView<Complaint>
      title="Complaints"
      noun={["complaint", "complaints"]}
      endpoint="/api/manufacturing/complaints/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/complaints/${row.id}`}
      searchHint="Number, customer, description"
      facets={[{ label: "Open", params: { status: "open" } }]}
      create={{ href: "/quality/complaints/new", permission: "manufacturing.add_complaint" }}
    />
  );
}
