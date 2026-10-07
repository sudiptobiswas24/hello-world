import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { ApiError, list, type Page, type Query } from "../api/client";
import { useAct } from "../api/hooks";
import { csvText, downloadCsv } from "../lib/csv";
import { count, date, money, quantity } from "../lib/format";
import { useAccess } from "../auth/me";
import { ErrorPanel } from "../shell/ErrorPanel";
import { Grouped } from "./Grouped";

export type Kind = "text" | "money" | "quantity" | "date" | "status";

export interface Column<T> {
  key: string;
  label: string;
  kind?: Kind;
  /** The ?ordering= field, when the server offers to sort by this column. */
  sort?: string;
  render?: (row: T) => ReactNode;
  width?: string;
}

/** A ready-made narrowing: one click sets these parameters. */
export interface Facet {
  label: string;
  params: Record<string, string>;
}

export interface ListViewProps<T> {
  title: string;
  endpoint: string;
  columns: Column<T>[];
  facets?: Facet[];
  rowKey?: (row: T) => string | number;
  rowHref?: (row: T) => string;
  searchHint?: string;
  /** Fixed parameters every page is asked with (?role_assignments__role=customer). */
  fixed?: Record<string, string>;
  /** "8 invoices" from the count. */
  noun: [string, string];
  /** A "New" button for whoever holds the permission. */
  create?: { href: string; permission: string };
  /** A run over many records (send the reminders now due), asked first in words. */
  actions?: ListAction[];
}

export interface ListAction {
  label: string;
  permission: string;
  /** Where it posts. */
  path: string;
  confirm: string;
  done: string | ((result: unknown) => string);
}

const SIZES = [50, 100, 200];
const RESERVED = new Set(["q", "page", "size", "ordering"]);

export function cell<T>(column: Column<T>, row: T): ReactNode {
  if (column.render) return column.render(row);
  const value = (row as Record<string, unknown>)[column.key];
  switch (column.kind) {
    case "money":
      return money(value as string);
    case "quantity":
      return quantity(value as string);
    case "date":
      return date(value as string);
    case "status":
      return value ? <span className={`pill pill-${String(value)}`}>{String(value).replace(/_/g, " ")}</span> : "";
    default:
      return value === null || value === undefined ? "" : String(value);
  }
}

/**
 * A list of documents as the server pages it: search, ready-made
 * narrowings, sorting where the server offers it, and a pager that knows
 * the true count.
 *
 * Everything that shapes the list lives in the address, so Back, a
 * refresh and a link sent to a colleague all show the same rows. Typing
 * waits a quarter second before asking; the page being replaced stays
 * on screen until the new one arrives, and the next page is fetched
 * ahead so paging forward is immediate.
 */
export function ListView<T>(props: ListViewProps<T>) {
  const { title, endpoint, columns, facets = [], rowKey, rowHref, searchHint, fixed, noun, create, actions = [] } = props;
  const { can } = useAccess();
  const act = useAct<unknown>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const searchBox = useRef<HTMLInputElement>(null);

  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const size = SIZES.includes(Number(params.get("size"))) ? Number(params.get("size")) : 50;
  const ordering = params.get("ordering") ?? "";
  const narrowing: Record<string, string> = {};
  params.forEach((value, key) => {
    if (!RESERVED.has(key)) narrowing[key] = value;
  });

  const [typed, setTyped] = useState(q);
  const [grouping, setGrouping] = useState(false);
  useEffect(() => setTyped(q), [q]);
  useEffect(() => {
    if (typed === q) return;
    const timer = window.setTimeout(() => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        if (typed) next.set("q", typed);
        else next.delete("q");
        next.delete("page");
        return next;
      }, { replace: true });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [typed, q, setParams]);

  const query: Query = useMemo(
    () => ({ ...fixed, ...narrowing, search: q, page, page_size: size, ordering }),
    // narrowing is rebuilt each render; what it holds is in params.
    [fixed, params.toString()],
  );
  const key = ["list", endpoint, query];
  const result = useQuery<Page<T>, ApiError>({
    queryKey: key,
    queryFn: ({ signal }) => list<T>(endpoint, query, signal),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });

  // A page past the end (a link kept while the list shrank) is not an
  // error to show: go to the first page and let the pager say how many.
  useEffect(() => {
    if (result.error?.status === 404 && page > 1) {
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.delete("page");
        return next;
      }, { replace: true });
    }
  }, [result.error, page, setParams]);

  const total = result.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / size));
  useEffect(() => {
    if (!result.data || page >= pages) return;
    const ahead = { ...query, page: page + 1 };
    void queryClient.prefetchQuery({
      queryKey: ["list", endpoint, ahead],
      queryFn: ({ signal }) => list<T>(endpoint, ahead, signal),
      staleTime: 15_000,
    });
  }, [result.data, page, pages, query, endpoint, queryClient]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchBox.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const set = (changes: Record<string, string | null>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [name, value] of Object.entries(changes)) {
        if (value === null || value === "") next.delete(name);
        else next.set(name, value);
      }
      if (!("page" in changes)) next.delete("page");
      return next;
    });

  const facetOn = (facet: Facet) => Object.entries(facet.params).every(([k, v]) => params.get(k) === v);
  const toggleFacet = (facet: Facet) => {
    const on = facetOn(facet);
    set(Object.fromEntries(Object.entries(facet.params).map(([k, v]) => [k, on ? null : v])));
  };

  const sortBy = (column: Column<T>) => {
    if (!column.sort) return;
    const next = ordering === column.sort ? `-${column.sort}` : ordering === `-${column.sort}` ? null : column.sort;
    set({ ordering: next });
  };

  const first = total === 0 ? 0 : (page - 1) * size + 1;
  const last = Math.min(page * size, total);
  const rows = result.data?.rows ?? [];
  const filtered = Boolean(q) || Object.keys(narrowing).length > 0;
  // Every row the list would show, page after page, as a file: what the
  // screen shows and nothing it does not.
  const [exporting, setExporting] = useState(false);
  const exportAll = async () => {
    setExporting(true);
    try {
      const all: T[] = [];
      for (let next = 1; ; next += 1) {
        const got = await list<T>(endpoint, { ...query, page: next, page_size: 500 });
        all.push(...got.rows);
        if (all.length >= got.total || got.rows.length === 0) break;
      }
      downloadCsv(title, csvText(columns, all as unknown as Record<string, unknown>[]));
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="list" aria-busy={result.isFetching}>
      <header className="list-head">
        <h1>{title}</h1>
        {create && can(create.permission) && (
          <Link className="btn primary" to={create.href}>New</Link>
        )}
        {actions.filter((action) => can(action.permission)).map((action) => (
          <button key={action.label} type="button" className="btn" disabled={act.pending} onClick={() => {
            if (!window.confirm(action.confirm)) return;
            void act.run("POST", action.path, {}, { done: action.done });
          }}>{action.label}</button>
        ))}
        {total > 0 && (
          <button type="button" className="btn" disabled={exporting} onClick={() => void exportAll()}>CSV</button>
        )}
        {total > 0 && (
          <button type="button" className="btn" aria-pressed={grouping} onClick={() => setGrouping((on) => !on)}>Group</button>
        )}
        <div className="search">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
          <input
            ref={searchBox}
            type="search"
            value={typed}
            placeholder={searchHint ?? "Search"}
            aria-label={`Search ${title.toLowerCase()}`}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setTyped("");
            }}
          />
          <kbd title="Press / to search">/</kbd>
        </div>
        <nav className="pager" aria-label="Pages">
          <span className="count">
            {result.isPending ? "…" : total === 0 ? `No ${noun[1]}` : `${count(first)}–${count(last)} of ${count(total)}`}
          </span>
          <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>‹</button>
          <button type="button" aria-label="Next page" disabled={page >= pages} onClick={() => set({ page: String(page + 1) })}>›</button>
          <select aria-label="Rows a page" value={size} onChange={(event) => set({ size: event.target.value })}>
            {SIZES.map((option) => <option key={option} value={option}>{option} a page</option>)}
          </select>
        </nav>
      </header>
      {facets.length > 0 && (
        <div className="facets" role="group" aria-label="Narrow the list">
          {facets.map((facet) => (
            <button
              key={facet.label}
              type="button"
              className={facetOn(facet) ? "facet on" : "facet"}
              aria-pressed={facetOn(facet)}
              onClick={() => toggleFacet(facet)}
            >
              {facet.label}
            </button>
          ))}
          {filtered && (
            <button type="button" className="facet clear" onClick={() => setParams(new URLSearchParams())}>
              Clear
            </button>
          )}
        </div>
      )}
      {grouping && <Grouped title={title} endpoint={endpoint} narrowing={{ ...fixed, ...narrowing, search: q }} />}
      <div className={result.isFetching && !result.isPending ? "progress on" : "progress"} />
      {result.isError ? (
        <ErrorPanel error={result.error} retry={() => void result.refetch()} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((column) => {
                  const direction = ordering === column.sort ? "ascending" : ordering === `-${column.sort}` ? "descending" : "none";
                  return (
                    <th
                      key={column.key}
                      scope="col"
                      className={`k-${column.kind ?? "text"}${column.sort ? " sortable" : ""}`}
                      style={column.width ? { width: column.width } : undefined}
                      aria-sort={column.sort ? direction : undefined}
                    >
                      {column.sort ? (
                        <button type="button" onClick={() => sortBy(column)}>
                          {column.label}
                          <span className={`arrow ${direction}`} aria-hidden="true" />
                        </button>
                      ) : column.label}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {result.isPending
                ? Array.from({ length: 8 }, (_, i) => (
                    <tr key={i} className="skeleton">
                      {columns.map((column) => <td key={column.key}><span /></td>)}
                    </tr>
                  ))
                : rows.map((row, index) => {
                    const href = rowHref?.(row);
                    return (
                      <tr
                        key={rowKey ? rowKey(row) : index}
                        className={href ? "link" : undefined}
                        onClick={(event) => {
                          if (!href || (event.target as HTMLElement).closest("a,button")) return;
                          if (event.metaKey || event.ctrlKey) window.open(`/app${href}`, "_blank", "noopener");
                          else navigate(href);
                        }}
                      >
                        {columns.map((column, i) => (
                          <td key={column.key} className={`k-${column.kind ?? "text"}`}>
                            {i === 0 && href ? <Link to={href}>{cell(column, row)}</Link> : cell(column, row)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
            </tbody>
          </table>
          {!result.isPending && rows.length === 0 && (
            <div className="empty">
              {filtered ? (
                <>
                  <p>No {noun[1]} match{q ? <> “{q}”</> : " these filters"}.</p>
                  <button type="button" onClick={() => setParams(new URLSearchParams())}>Show all {noun[1]}</button>
                </>
              ) : (
                <p>No {noun[1]} yet.</p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
