import { useAct, useGet } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton } from "../../forms/Document";
import { date } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { DataTable } from "../../views/DataTable";

interface Schedule { id: number; name: string; serves: string; due_on: string | null; hours_remaining: string | null; [key: string]: unknown }

/** What has run out on either clock and has no job on the board yet. */
export default function Due() {
  const { can } = useAccess();
  const due = useGet<Schedule[]>("/api/manufacturing/maintenance-schedules/due/");
  const act = useAct<{ id: number }>();
  if (due.isError) return <ErrorPanel error={due.error} retry={() => void due.refetch()} />;
  return (
    <section className="report">
      <header className="list-head"><h1>Maintenance due</h1></header>
      <DataTable<Schedule>
        rows={due.data ?? []}
        pending={due.isPending}
        empty="Nothing is due."
        href={(row) => `/plant/schedules/${row.id}`}
        columns={[
          { key: "name", label: "Work" },
          { key: "serves", label: "Where" },
          { key: "due_on", label: "Due by the calendar", width: "11rem", render: (row) => date(row.due_on) || "—" },
          { key: "hours_remaining", label: "Running hours left", width: "10rem", kind: "quantity" },
          { key: "raise", label: "", width: "9rem", render: (row) => can("manufacturing.add_maintenancejob") ? (
            <ActionButton pending={act.pending} onClick={() => void act.run("POST",
              `/api/manufacturing/maintenance-schedules/${row.id}/raise_job/`, {}, { done: "Job raised" })}>
              Raise the job
            </ActionButton>
          ) : null },
        ]}
      />
    </section>
  );
}
