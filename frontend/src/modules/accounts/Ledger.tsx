import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router";

import { ApiError, request } from "../../api/client";
import { DocHeader } from "../../forms/Document";
import { positive } from "../../lib/decimal";
import { count, date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Line {
  id: number;
  entry: number;
  date: string;
  reference: string;
  memo: string;
  party: string;
  debit: string;
  credit: string;
  balance: string;
}

interface Ledger {
  account: { id: number; code: string; name: string; account_type: string };
  opening: string;
  debit: string;
  credit: string;
  closing: string;
  lines: Line[];
}

const SIZE = 50;

/**
 * An account's ledger for a period: what it opened at, every posted line
 * newest first with the balance after it, and what it closed at. The
 * balances are the server's, so page five is as right as page one.
 */
export default function LedgerPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const query = { from, to, page: String(page), page_size: String(SIZE) };
  const ledger = useQuery<{ body: Ledger; total: number }, ApiError>({
    queryKey: ["ledger", id, query],
    queryFn: async ({ signal }) => {
      const reply = await request<Ledger>(`/api/accounting/accounts/${id}/ledger/`, { query, signal });
      return { body: reply.data, total: Number(reply.headers.get("X-Total-Count") ?? 0) };
    },
    placeholderData: (previous) => previous,
  });

  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    return next;
  });

  if (ledger.isError) return <ErrorPanel error={ledger.error} retry={() => void ledger.refetch()} />;
  const data = ledger.data?.body;
  const total = ledger.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / SIZE));

  return (
    <article className="doc">
      <DocHeader back="/accounts/chart" backLabel="Chart of accounts" title="Ledger"
        number={data ? `${data.account.code} · ${data.account.name}` : "…"} />
      <header className="list-head">
        <label className="inline">From <input type="date" value={from} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="inline">To <input type="date" value={to} onChange={(e) => set("to", e.target.value)} /></label>
      </header>
      <div className="tiles">
        <div className="tile"><span>Opened at</span><strong>{data ? money(data.opening) : "…"}</strong></div>
        <div className="tile"><span>Debits</span><strong>{data ? money(data.debit) : "…"}</strong></div>
        <div className="tile"><span>Credits</span><strong>{data ? money(data.credit) : "…"}</strong></div>
        <div className="tile"><span>Closed at</span><strong>{data ? money(data.closing) : "…"}</strong></div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Date</th><th scope="col">Entry</th><th scope="col">What</th><th scope="col">Party</th>
              <th scope="col" className="k-money">Debit</th><th scope="col" className="k-money">Credit</th>
              <th scope="col" className="k-money">Balance</th>
            </tr>
          </thead>
          <tbody>
            {!data ? <tr className="skeleton"><td colSpan={7}><span /></td></tr> : data.lines.map((line) => (
              <tr key={line.id}>
                <td>{date(line.date)}</td>
                <td><Link to={`/accounts/journals/${line.entry}`}>{line.reference || `#${line.entry}`}</Link></td>
                <td>{line.memo}</td>
                <td>{line.party}</td>
                <td className="k-money">{positive(line.debit) ? money(line.debit) : ""}</td>
                <td className="k-money">{positive(line.credit) ? money(line.credit) : ""}</td>
                <td className="k-money">{money(line.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.lines.length === 0 && <div className="empty"><p>Nothing posted in this period.</p></div>}
      </div>
      <nav className="pager" aria-label="Pages">
        <span className="count">{count(total)} lines</span>
        <button type="button" className="btn" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>Newer</button>
        <span>{page} of {pages}</span>
        <button type="button" className="btn" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>Older</button>
      </nav>
    </article>
  );
}
