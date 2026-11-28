import { Link } from "react-router";

import { useGet } from "../../api/hooks";
import { dateTime } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Slot {
  work_order: number; run: string; item: string; operation: string; sequence: number;
  start: string; finish: string; late: boolean | null; due: string | null; held: boolean | string | null;
}

const ROW = 34;
const LABEL = 160;
const WIDTH = 1000;
const RIGHT = 130; // room for the last time on the axis and a label beside a bar that ends late
const HOUR = 3_600_000;
const ON_TIME = "#2a6fb0";
const LATE = "#c0392b";

/**
 * The machine schedule as bars on one time line: each machine a row,
 * each step a bar from its start to its finish, what finishes after it
 * is due in red. The same schedule the table shows, drawn.
 *
 * Said on the page (O393): what the two colours mean, how many held steps
 * are not drawn (they were left out without a word), and a bar too narrow
 * for its run's number carries it beside it rather than nowhere.
 */
export default function Gantt() {
  const board = useGet<Record<string, Slot[]>>("/api/manufacturing/dispatch/");
  if (board.isError) return <ErrorPanel error={board.error} retry={() => void board.refetch()} />;
  const machines = Object.entries(board.data ?? {});
  const every = machines.flatMap(([, rows]) => rows);
  const slots = every.filter((slot) => !slot.held);
  const held = every.length - slots.length;
  const times = slots.flatMap((slot) => [Date.parse(slot.start), Date.parse(slot.finish)]).filter((t) => !Number.isNaN(t));
  const from = times.length ? Math.min(...times) : Date.now();
  const to = times.length ? Math.max(from + HOUR, ...times) : from + 24 * HOUR;
  const scale = (WIDTH - LABEL - RIGHT) / (to - from);
  const x = (t: number) => LABEL + (t - from) * scale;
  const ticks: number[] = [];
  const step = (to - from) > 3 * 24 * HOUR ? 24 * HOUR : (to - from) > 12 * HOUR ? 6 * HOUR : HOUR;
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) ticks.push(t);
  const height = ROW * (machines.length + 1) + 10;
  return (
    <section className="report">
      <header className="list-head">
        <h1>Machine Gantt</h1>
        <Link className="btn" to="/production/schedule">As a table</Link>
      </header>
      {machines.length > 0 && (
        <p className="legend">
          <span><i style={{ background: ON_TIME }} /> Finishes by its due date</span>
          <span><i style={{ background: LATE }} /> Finishes after it is due</span>
          {held > 0 && <span className="muted">{held} held {held === 1 ? "step is" : "steps are"} not drawn: see the table</span>}
        </p>
      )}
      {board.isPending && <div className="loading">Scheduling…</div>}
      {board.data && machines.length === 0 && <div className="empty"><p>Nothing released to schedule.</p></div>}
      {machines.length > 0 && (
        <div className="sheet" style={{ overflowX: "auto" }}>
          <svg role="img" aria-label="Machine Gantt" width={WIDTH} height={height} viewBox={`0 0 ${WIDTH} ${height}`} style={{ fontSize: "12px" }}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={x(t)} y1={ROW - 6} x2={x(t)} y2={height} stroke="#ddd" />
                <text x={x(t) + 2} y={ROW - 10} fill="#777">{dateTime(new Date(t).toISOString())}</text>
              </g>
            ))}
            {machines.map(([machine, rows], index) => {
              const y = ROW * (index + 1) + 4;
              return (
                <g key={machine}>
                  <text x={4} y={y + ROW / 2} dominantBaseline="middle" fontWeight="bold">{machine}</text>
                  {rows.filter((slot) => !slot.held).map((slot) => {
                    const start = Date.parse(slot.start);
                    const finish = Date.parse(slot.finish);
                    const left = x(start);
                    const width = Math.max(2, x(finish) - left);
                    return (
                      <g key={`${slot.work_order}-${slot.sequence}`}>
                        <title>{`${slot.run} · ${slot.item} · ${slot.operation}: ${dateTime(slot.start)} to ${dateTime(slot.finish)}${slot.due ? `, due ${slot.due}` : ""}${slot.late ? " (late)" : ""}`}</title>
                        <rect x={left} y={y + 4} width={width} height={ROW - 12} rx={3} fill={slot.late ? LATE : ON_TIME} opacity={0.85} />
                        {width > 110
                          ? <text x={left + 4} y={y + ROW / 2} dominantBaseline="middle" fill="#fff">{slot.run}</text>
                          : <text x={left + width + 4} y={y + ROW / 2} dominantBaseline="middle" fill="#333">{slot.run}</text>}
                      </g>
                    );
                  })}
                </g>
              );
            })}
          </svg>
        </div>
      )}
    </section>
  );
}
