import { Link } from "react-router";

import type { Query } from "../api/client";
import { useRows } from "../api/hooks";
import { cell, type Column } from "./ListView";

export type { Column } from "./ListView";

/**
 * A plain table: rows already in hand (a report, a record's own lines),
 * or one endpoint's rows read for a panel. No paging: a panel shows up
 * to two hundred and says so when there are more to see in the list.
 */
export function DataTable<T extends Record<string, unknown>>(props: {
  columns: Column<T>[];
  rows?: T[];
  endpoint?: string;
  query?: Query;
  href?: (row: T) => string;
  empty?: string;
  pending?: boolean;
  caption?: string;
  foot?: (string | null)[];
}) {
  const { columns, href, empty = "Nothing to show.", caption, foot } = props;
  const read = useRows<T>(props.endpoint ?? "", props.query ?? {}, Boolean(props.endpoint) && !props.rows);
  const rows = props.rows ?? read.data ?? [];
  const pending = props.pending ?? (Boolean(props.endpoint) && !props.rows && read.isPending);
  if (read.isError) return <p className="muted">Could not read them: {read.error.message}</p>;
  return (
    <div className="table-wrap">
      <table>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={`k-${column.kind ?? "text"}`}
                style={column.width ? { width: column.width } : undefined}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pending
            ? <tr className="skeleton">{columns.map((column) => <td key={column.key}><span /></td>)}</tr>
            : rows.map((row, index) => (
                <tr key={(row.id as number | string | undefined) ?? index}>
                  {columns.map((column, i) => (
                    <td key={column.key} className={`k-${column.kind ?? "text"}`}>
                      {i === 0 && href ? <Link to={href(row)}>{cell(column, row)}</Link> : cell(column, row)}
                    </td>
                  ))}
                </tr>
              ))}
        </tbody>
        {foot && rows.length > 0 && (
          <tfoot>
            <tr>{foot.map((value, i) => <td key={i} className={`k-${columns[i]?.kind ?? "text"}`}>{value ?? ""}</td>)}</tr>
          </tfoot>
        )}
      </table>
      {!pending && rows.length === 0 && <div className="empty"><p>{empty}</p></div>}
      {!props.rows && rows.length >= 200 && <p className="muted">The first 200 are shown.</p>}
    </div>
  );
}
