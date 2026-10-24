import { Link, useSearchParams } from "react-router";

import { useGet, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Job { id: number; title: string; where: string; is_breakdown: boolean; done: boolean; overdue: boolean; due_on: string }
interface Due { id: number; name: string; where: string; overdue: boolean }
interface Day { date: string; jobs: Job[]; due: Due[] }
interface Month { year: number; month: number; days: Day[] }
interface Centre { id: number; code: string; name: string }

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * The month as the maintenance department reads it: each day with the
 * jobs due or done on it and the schedules whose clock runs out on it
 * with no job raised yet. Breakdowns in red, what is late in bold, what
 * is done struck through.
 */
export default function Calendar() {
  const [search, setSearch] = useSearchParams();
  const { can } = useAccess();
  const now = new Date();
  const year = Number(search.get("year") || now.getFullYear());
  const month = Number(search.get("month") || now.getMonth() + 1);
  const centre = search.get("work_centre") || "";
  const centres = useReference<Centre>("/api/manufacturing/work-centres/", undefined, can("manufacturing.view_workcentre"));
  const data = useGet<Month>("/api/manufacturing/maintenance-jobs/calendar/", { year: String(year), month: String(month), ...(centre ? { work_centre: centre } : {}) });
  const go = (y: number, m: number) => {
    const next = new URLSearchParams(search);
    if (m < 1) { y -= 1; m = 12; }
    if (m > 12) { y += 1; m = 1; }
    next.set("year", String(y));
    next.set("month", String(m));
    setSearch(next, { replace: true });
  };
  const choose = (value: string) => {
    const next = new URLSearchParams(search);
    if (value) next.set("work_centre", value);
    else next.delete("work_centre");
    setSearch(next, { replace: true });
  };
  if (data.isError) return <ErrorPanel error={data.error} retry={() => void data.refetch()} />;
  const days = data.data?.days ?? [];
  const first = days[0]?.date;
  const lead = first ? (new Date(`${first}T00:00:00`).getDay() + 6) % 7 : 0;
  const cells: (Day | null)[] = [...Array<null>(lead).fill(null), ...days];
  while (cells.length % 7) cells.push(null);
  const title = new Date(year, month - 1, 1).toLocaleString("en-IN", { month: "long", year: "numeric" });
  return (
    <section className="report">
      <header className="list-head">
        <h1>Maintenance calendar</h1>
        <div className="actions">
          <button type="button" className="btn" onClick={() => go(year, month - 1)} aria-label="Previous month">‹</button>
          <strong aria-live="polite">{title}</strong>
          <button type="button" className="btn" onClick={() => go(year, month + 1)} aria-label="Next month">›</button>
          <select aria-label="Work centre" value={centre} onChange={(event) => choose(event.target.value)}>
            <option value="">Every work centre</option>
            {(centres.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}
          </select>
        </div>
      </header>
      {data.isPending && <div className="loading">Reading the month…</div>}
      {days.length > 0 && (
        <div className="sheet" style={{ overflowX: "auto" }}>
          <table className="calendar" style={{ width: "100%", tableLayout: "fixed" }}>
            <thead><tr>{WEEKDAYS.map((day) => <th key={day} scope="col">{day}</th>)}</tr></thead>
            <tbody>
              {Array.from({ length: cells.length / 7 }, (_, week) => (
                <tr key={week}>
                  {cells.slice(week * 7, week * 7 + 7).map((cell, index) => (
                    <td key={index} style={{ verticalAlign: "top", height: "5.5rem", border: "1px solid var(--line, #ddd)", padding: "0.25rem" }}>
                      {cell && (
                        <>
                          <div className="muted">{Number(cell.date.slice(8, 10))}</div>
                          {cell.due.map((row) => (
                            <div key={`s${row.id}`} style={{ fontWeight: row.overdue ? "bold" : undefined }}>
                              <Link to={`/plant/schedules/${row.id}`}>{row.name}</Link> <span className="muted">{row.where} · due</span>
                            </div>
                          ))}
                          {cell.jobs.map((row) => (
                            <div key={`j${row.id}`} style={{ color: row.is_breakdown ? "#c0392b" : undefined, fontWeight: row.overdue ? "bold" : undefined,
                              textDecoration: row.done ? "line-through" : undefined }}>
                              <Link to={`/plant/jobs/${row.id}`}>{row.title}</Link> <span className="muted">{row.where}</span>
                            </div>
                          ))}
                        </>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
