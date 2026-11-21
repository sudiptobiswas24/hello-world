import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "valid_from", label: "From", kind: "date", width: "8rem", sort: "valid_from" },
  { key: "freight_percent", label: "Freight %", width: "7rem" },
  { key: "insurance_percent", label: "Insurance %", width: "7rem" },
  { key: "confirmed", label: "Confirmed", width: "7rem", render: (row) => (row.confirmed ? "Yes" : "Not yet") },
  { key: "citation", label: "Rule" },
];

/**
 * What a bill of entry takes freight and insurance as when the actual figures are not known
 * (Customs Valuation Rules 2007, r.10(2)). Loaded unconfirmed; the customs broker confirms them here,
 * and the bill of entry prints "unconfirmed" beside a figure that is not.
 */
export default function CustomsValuationDefaults() {
  return (
    <ListView<Row>
      title="Customs valuation defaults"
      noun={["default", "defaults"]}
      endpoint="/api/purchasing/customs-valuation-defaults/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/customs-valuation/${row.id}`}
      create={{ href: "/purchasing/customs-valuation/new", permission: "purchasing.add_customsvaluationdefault" }}
    />
  );
}
