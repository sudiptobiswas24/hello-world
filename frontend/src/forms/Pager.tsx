import { count } from "../lib/format";

/**
 * Which part of a list a panel shows, and the way to the rest. Shown only
 * when there is more than one page: a panel that quietly showed the first
 * 200 of 600 read as if it were all of them.
 */
export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (page: number) => void }) {
  if (total <= size) return null;
  const last = Math.ceil(total / size);
  return (
    <nav className="pager" aria-label="Pages">
      <span className="count">{count((page - 1) * size + 1)}–{count(Math.min(page * size, total))} of {count(total)}</span>
      <button type="button" className="btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <button type="button" className="btn" disabled={page >= last} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  );
}
