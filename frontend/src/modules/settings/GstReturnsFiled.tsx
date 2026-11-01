import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "period", label: "Month", width: "8rem", sort: "period" },
  { key: "return_name", label: "Return", width: "8rem" },
  { key: "filed_on", label: "Filed on", kind: "date", width: "8rem", sort: "filed_on" },
  { key: "arn", label: "ARN" },
];

/** GST returns as filed on the portal: what a filed GSTR-3B claimed is not taken back from under it. */
export default function GstReturnsFiled() {
  return (
    <ListView<Row>
      title="GST returns filed"
      noun={["return", "returns"]}
      endpoint="/api/accounting/gst-returns-filed/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/gst-returns-filed/${row.id}`}
      create={{ href: "/settings/gst-returns-filed/new", permission: "accounting.add_gstreturnfiled" }}
    />
  );
}
