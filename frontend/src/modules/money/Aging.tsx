import { Link, useSearchParams } from "react-router";

import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { sum } from "../../lib/decimal";
import { count, date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Entry {
  id: number;
  number: string;
  customer?: string;
  vendor?: string;
  due_date: string;
  days_overdue: number;
  /** In the base currency, at the rate the document posted at (O416). */
  amount_due: string;
  /** The document's own currency and what is due in it; empty on a restatement's line. */
  currency?: string;
  due_in_currency?: string | null;
  /** On a restatement's line: what it is. */
  description?: string;
  /** invoice, bill, credit_note, debit_note, receipt, payment (O412) or restatement (O416). */
  kind?: string;
}
type Buckets = Record<string, { count: number; total: string; invoices?: Entry[]; bills?: Entry[] }>;

export interface AgingConfig {
  title: string; // "Receivables by age"
  endpoint: string;
  documents: "invoices" | "bills";
  document: string; // "Invoice"
  party: "customer" | "vendor";
  href: (id: number) => string;
}

const ORDER = ["current", "1-30", "31-60", "61-90", "90+"];
// What the party is owed back (an open note) or has paid ahead (money no document has taken), negative: without
// it the total was not the ledger's, an open credit note of 27,187.20 above it (O412).
const ALL = [...ORDER, "credits"];
// On a closing-rate restatement's own day the ledger carries it, so the report does too, one line per control
// account; the next day it has reversed and there is none (O416).
const RESTATED = "restated";
const LABEL: Record<string, string> = { current: "Not yet due", "1-30": "1–30 days late", "31-60": "31–60", "61-90": "61–90", "90+": "Over 90", credits: "Credits not applied", restated: "Restated at the closing rate" };

/** What is owed, by how late it is, as at a date: to us or by us. */
export function AgingReport({ title, endpoint, documents, document, party, href }: AgingConfig) {
  const [params, setParams] = useSearchParams();
  const asOf = params.get("as_of") ?? today();
  const shown = params.get("bucket");
  const report = useGet<Buckets>(endpoint, { as_of: asOf });

  const set = (key: string, value: string | null) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });

  const data = report.data;
  const keys = data && data[RESTATED] ? [...ALL, RESTATED] : ALL;
  const overdue = data ? sum(ORDER.slice(1).map((key) => data[key]?.total)) : "0";
  const owed = data ? sum(keys.map((key) => data[key]?.total)) : "0";
  const rows = data ? (shown ? data[shown]?.[documents] ?? [] : keys.flatMap((key) => data[key]?.[documents] ?? [])) : [];
  // Money on account opens its receipt or payment, not a document of the list.
  const open = (row: Entry) => row.kind === "receipt" ? `/sales/receipts/${row.id}`
    : row.kind === "payment" ? `/purchasing/payments/${row.id}` : href(row.id);
  // A document in a foreign currency shows what is due in it beside the rupees: the bucket totals are base.
  // A base-currency document's two figures are one figure, so it shows none here.
  const ownCurrency = (row: Entry) => row.currency && row.due_in_currency != null && row.due_in_currency !== row.amount_due
    ? `${row.currency} ${money(row.due_in_currency)}` : "";

  return (
    <section className="report">
      <header className="list-head">
        <h1>{title}</h1>
        <label className="inline">As at <input type="date" value={asOf} onChange={(e) => set("as_of", e.target.value)} /></label>
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <>
          <div className="tiles">
            <div className="tile"><span>Owed in all</span><strong>{data ? money(owed) : "…"}</strong></div>
            <div className="tile bad"><span>Overdue</span><strong>{data ? money(overdue) : "…"}</strong></div>
            {keys.map((key) => (
              <button key={key} type="button" className={`tile pick${shown === key ? " on" : ""}`} aria-pressed={shown === key}
                onClick={() => set("bucket", shown === key ? null : key)}>
                <span>{LABEL[key]}</span>
                <strong>{data ? money(data[key]?.total) : "…"}</strong>
                <small>{data ? `${count(data[key]?.count ?? 0)} ${key === "credits" ? "open" : "due"}` : ""}</small>
              </button>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">{document}</th><th scope="col">{party === "customer" ? "Customer" : "Vendor"}</th><th scope="col">Due</th><th scope="col" className="k-quantity">Days late</th><th scope="col" className="k-money">In its currency</th><th scope="col" className="k-money">Owed</th></tr></thead>
              <tbody>
                {report.isPending ? <tr className="skeleton"><td colSpan={6}><span /></td></tr> : rows.map((row, index) => (
                  <tr key={`${row.id}-${index}`}>
                    {/* A restatement is the accountant's page, which an AR or AP manager may not open: named, not linked. */}
                    <td>{row.kind === "restatement" ? row.number : <Link to={open(row)}>{row.number}</Link>}</td>
                    <td>{row.kind === "restatement" ? row.description : row[party]}</td>
                    <td>{date(row.due_date)}</td>
                    <td className="k-quantity">{row.days_overdue || ""}</td>
                    <td className="k-money">{ownCurrency(row)}</td>
                    <td className="k-money">{money(row.amount_due)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && rows.length === 0 && <div className="empty"><p>Nothing owed{shown ? " in this band" : ""}.</p></div>}
          </div>
        </>
      )}
    </section>
  );
}
