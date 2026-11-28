import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "financial_year", label: "Year", width: "7rem" },
  { key: "arn", label: "ARN" },
  { key: "filed_on", label: "Filed on", kind: "date", width: "8rem" },
  { key: "valid_from", label: "From", kind: "date", width: "8rem" },
  { key: "valid_to", label: "To", kind: "date", width: "8rem" },
];

/** The Letter of Undertaking for each financial year: an export without IGST is posted under the one in force. */
export default function Luts() {
  return (
    <ListView<Row>
      title="Letters of undertaking"
      noun={["LUT", "LUTs"]}
      endpoint="/api/accounting/letters-of-undertaking/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/luts/${row.id}`}
      create={{ href: "/settings/luts/new", permission: "accounting.add_letterofundertaking" }}
    />
  );
}
