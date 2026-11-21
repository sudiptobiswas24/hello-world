import type { ReactNode } from "react";
import { useSearchParams } from "react-router";

import type { Query } from "../api/client";
import { useGet, useReference } from "../api/hooks";
import { useAccess } from "../auth/me";
import { csvText, downloadCsv } from "../lib/csv";
import { today } from "../forms/fields";
import { ErrorPanel } from "../shell/ErrorPanel";
import { DataTable, type Column } from "./DataTable";

type Row = Record<string, unknown>;

/**
 * What a report is asked with. A `ref` choice lists a short master and
 * sends one of its fields (a code, usually), since the report endpoints
 * take codes; `required` holds the report back until it is chosen.
 */
export interface ParamDef {
  key: string;
  label: string;
  kind: "date" | "choice" | "ref";
  choices?: [string, string][];
  ref?: { endpoint: string; label: (row: Row) => string; value: (row: Row) => string; permission?: string };
  /** What it starts at: a date ("today", "month-start", "-30") or a choice. */
  initial?: string;
  required?: boolean;
}

function startingAt(initial: string | undefined): string {
  if (!initial) return "";
  const now = today();
  if (initial === "today") return now;
  if (initial === "month-start") return `${now.slice(0, 8)}01`;
  if (/^-\d+$/.test(initial)) {
    const [y, m, d] = now.split("-").map(Number) as [number, number, number];
    const day = new Date(Date.UTC(y, m - 1, d - Number(initial.slice(1))));
    return day.toISOString().slice(0, 10);
  }
  return initial;
}

function RefChoice({ param, value, set }: { param: ParamDef; value: string; set: (v: string) => void }) {
  const { can } = useAccess();
  const rows = useReference<Row & { id: number }>(param.ref!.endpoint, undefined, !param.ref!.permission || can(param.ref!.permission));
  return (
    <label className="inline">{param.label}{" "}
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">{param.required ? "Choose…" : "All"}</option>
        {(rows.data ?? []).map((row) => <option key={row.id} value={param.ref!.value(row)}>{param.ref!.label(row)}</option>)}
      </select>
    </label>
  );
}

/**
 * A report: what it is asked with across the top, kept in the address so
 * a link shows the same figures, and its rows below.
 */
export function ReportView<D, T extends Row>(props: {
  title: string;
  /** The endpoint, which may depend on a parameter (a work centre's id). */
  endpoint: string | ((values: Record<string, string>) => string | null);
  params: ParamDef[];
  /** Parameters as the server names them, when not as the screen does. */
  send?: (values: Record<string, string>) => Query;
  rows: (data: D) => T[];
  columns: Column<T>[];
  foot?: (rows: T[], data: D) => (string | null)[];
  above?: (data: D) => ReactNode;
  empty?: string;
  waiting?: string;
}) {
  const { title, params, rows, columns, foot, above, empty, waiting } = props;
  const [search, setSearch] = useSearchParams();
  const values: Record<string, string> = {};
  for (const param of params) values[param.key] = search.get(param.key) ?? startingAt(param.initial);
  const ready = params.every((param) => !param.required || values[param.key]);
  const endpoint = typeof props.endpoint === "function" ? props.endpoint(values) : props.endpoint;
  const query: Query = props.send ? props.send(values) : Object.fromEntries(Object.entries(values).filter(([, v]) => v));
  const report = useGet<D>(endpoint ?? "", query, ready && Boolean(endpoint));

  const set = (key: string, value: string) => setSearch((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });

  const found = report.data ? rows(report.data) : [];
  return (
    <section className="report">
      <header className="list-head">
        <h1>{title}</h1>
        {found.length > 0 && (
          <button type="button" className="btn" onClick={() => downloadCsv(title, csvText(columns, found as unknown as Record<string, unknown>[]))}>CSV</button>
        )}
        {params.map((param) => param.kind === "date" ? (
          <label key={param.key} className="inline">{param.label}{" "}
            <input type="date" value={values[param.key]} onChange={(e) => set(param.key, e.target.value)} />
          </label>
        ) : param.kind === "choice" ? (
          <label key={param.key} className="inline">{param.label}{" "}
            <select value={values[param.key]} onChange={(e) => set(param.key, e.target.value)}>
              {!param.required && <option value="">All</option>}
              {param.choices!.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
        ) : (
          <RefChoice key={param.key} param={param} value={values[param.key] ?? ""} set={(v) => set(param.key, v)} />
        ))}
      </header>
      {!ready ? <div className="empty"><p>{waiting ?? "Choose what to report on."}</p></div>
        : report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} />
        : (
          <>
            {report.data && above?.(report.data)}
            <DataTable<T> rows={found} columns={columns} pending={report.isPending} empty={empty}
              foot={report.data && foot ? foot(found, report.data) : undefined} />
          </>
        )}
    </section>
  );
}
