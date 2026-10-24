import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem", render: (row) => String(row.number || "Draft") },
  { key: "description", label: "For" },
  { key: "issue_date", label: "Out on", kind: "date", width: "9rem" },
  { key: "response_due", label: "Answers by", kind: "date", width: "9rem" },
  { key: "invited", label: "Vendors", width: "7rem", render: (row) => String(((row.invited as unknown[]) ?? []).length) },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

export default function Rfqs() {
  return (
    <ListView<Row>
      title="Requests for quotation"
      noun={["request for quotation", "requests for quotation"]}
      endpoint="/api/purchasing/rfqs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/rfqs/${row.id}`}
      searchHint="Number, what for, or a vendor asked"
      create={{ href: "/purchasing/rfqs/new", permission: "purchasing.add_requestforquotation" }}
    />
  );
}
