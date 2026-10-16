import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row {
  vendor: string; order_lines: number; open_lines: number; quantity_ordered: string; quantity_received: string;
  quantity_returned: string; fill_rate: string | null; on_time_rate: string | null; average_days_late: string;
  return_rate: string | null; average_lead_days: string | null; price_variance: string; [key: string]: unknown;
}

const PARAMS: ParamDef[] = [
  { key: "start", label: "Orders from", kind: "date", initial: "-365" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

const rate = (value: string | null) => (value === null ? "—" : `${value}%`);

/**
 * How each vendor actually behaved on the orders placed in the span: on
 * time against the dates they were given, in full, what came back, how
 * long they take, and what they billed over the price agreed. A vendor
 * never given a date is not scored punctual.
 */
export default function VendorScorecard() {
  const columns: Column<Row>[] = [
    { key: "vendor", label: "Vendor" },
    { key: "order_lines", label: "Lines", kind: "quantity", width: "5rem" },
    { key: "quantity_ordered", label: "Ordered", kind: "quantity", width: "8rem" },
    { key: "quantity_received", label: "Received", kind: "quantity", width: "8rem" },
    { key: "fill_rate", label: "In full", width: "6rem", render: (row) => rate(row.fill_rate) },
    { key: "on_time_rate", label: "On time", width: "6rem", render: (row) => rate(row.on_time_rate) },
    { key: "average_days_late", label: "Days late", kind: "quantity", width: "6rem" },
    { key: "average_lead_days", label: "Days to deliver", width: "7rem", render: (row) => row.average_lead_days ?? "—" },
    { key: "quantity_returned", label: "Returned", kind: "quantity", width: "8rem" },
    { key: "return_rate", label: "Returned", width: "6rem", render: (row) => rate(row.return_rate) },
    { key: "price_variance", label: "Billed over price", kind: "money", width: "9rem" },
    { key: "open_lines", label: "Open lines", kind: "quantity", width: "6rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Vendor scorecard"
      endpoint="/api/purchasing/purchasing-reports/vendor-performance/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.vendor }))}
      columns={columns}
      empty="No order was placed in these days."
    />
  );
}
