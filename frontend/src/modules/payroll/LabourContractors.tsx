import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "party_name", label: "Contractor" },
  { key: "licence_number", label: "Licence" },
  { key: "licence_valid_to", label: "Valid to", kind: "date" },
  { key: "work_nature", label: "Work" },
  { key: "max_workers", label: "Up to", kind: "quantity" },
];

export default function LabourContractors() {
  return (
    <ListView<Row>
      title="Labour contractors"
      noun={["contractor", "contractors"]}
      endpoint="/api/hr/labour-contractors/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/labour-contractors/${row.id}`}
      searchHint="Contractor, licence or work"
      create={{ href: "/payroll/labour-contractors/new", permission: "hr.add_labourcontractor" }}
    />
  );
}
