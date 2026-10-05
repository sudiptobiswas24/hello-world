import { Link } from "react-router";

import type { Query } from "../api/client";
import { useRows } from "../api/hooks";
import { useAccess } from "../auth/me";

type Row = Record<string, unknown> & { id: number };

/**
 * The documents that came from this one: an order's deliveries and
 * invoices. Shown to whoever may read them, and not even asked for
 * otherwise: a rep reads orders but not deliveries, and a list the
 * server refuses is a red line in the log on every order opened.
 */
export function RelatedList({ title, endpoint, permission, query, href, cells }: {
  title: string;
  endpoint: string;
  /** What the server checks to read `endpoint`. */
  permission: string;
  query: Query;
  href: (row: Row) => string;
  cells: (row: Row) => string[];
}) {
  const { can } = useAccess();
  const allowed = can(permission);
  const rows = useRows<Row>(endpoint, query, allowed);
  if (!allowed) return null;
  return (
    <section className="related-list">
      <h2>{title}</h2>
      {rows.isPending ? <p className="muted">…</p>
        : rows.isError ? <p className="muted">Could not read them: {rows.error.message}</p>
        : rows.data.length === 0 ? <p className="muted">None yet.</p>
        : (
          <ul>
            {rows.data.map((row) => {
              const [first, ...rest] = cells(row);
              return (
                <li key={row.id}>
                  <Link to={href(row)}>{first}</Link>
                  {rest.map((cell, i) => <span key={i}>{cell}</span>)}
                </li>
              );
            })}
          </ul>
        )}
    </section>
  );
}
