import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";
import { postedLabel } from "./partyRefs";

interface Challan { id: number; number: string; challan_date: string; job_worker_name: string; vehicle: string; lines: unknown[];
  posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Challan>[] = [
  { key: "number", label: "Challan", sort: "number", width: "10rem" },
  { key: "challan_date", label: "Date", sort: "challan_date", width: "8rem", render: (row) => date(row.challan_date) },
  { key: "job_worker_name", label: "Job worker" },
  { key: "vehicle", label: "Vehicle", width: "10rem" },
  { key: "lines", label: "Lines", width: "5rem", render: (row) => String(row.lines.length) },
  { key: "posted", label: "", width: "6rem", render: postedLabel },
];

export default function JobWork() {
  return (
    <ListView<Challan>
      title="Job-work challans"
      noun={["job-work challan", "job-work challans"]}
      endpoint="/api/manufacturing/job-work-challans/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/job-work/${row.id}`}
      searchHint="Challan, job worker or vehicle"
      facets={[{ label: "Drafts", params: { posted: "false" } }]}
      create={{ href: "/stores/job-work/new", permission: "manufacturing.add_jobworkchallan" }}
    />
  );
}
