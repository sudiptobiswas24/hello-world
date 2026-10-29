import { useState } from "react";
import { Link } from "react-router";

import { useAct, useGet } from "../../api/hooks";
import { ActionButton } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { today } from "../../forms/fields";
import { sum } from "../../lib/decimal";
import { count, date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { StatementHead, usePeriod } from "./Statement";

interface Statement { id: number; period: string; gstin: string; generated_on: string | null; line_count: number; kept_by: string; created_at: string }
interface Figures { taxable: string; igst: string; cgst: string; sgst: string; cess: string; value: string }
interface BillSide extends Figures { id: number; number: string; vendor: string; reference: string; date: string; is_note: boolean }
interface LineSide extends Figures {
  id: number; statement: string; number: string; date: string | null; supplier_gstin: string; supplier_name: string;
  kind: string; itc_available: boolean; reason: string; reverse_charge: boolean; irn: string;
}
interface Pair { bill: BillSide | null; line: LineSide | null; difference?: Figures }
interface Report {
  period: string;
  statement: { id: number; period: string; gstin: string; generated_on: string | null; lines: number } | null;
  matched: Pair[]; differs: Pair[]; not_in_2b: Pair[]; not_booked: Pair[];
  totals: { filed: string; booked: string; matched: string; waiting: string; not_booked: string };
  notes: string[];
}

const STATEMENTS = "/api/gst/gstr2b/";
const KIND: Record<string, string> = { invoice: "Invoice", credit_note: "Credit note", debit_note: "Debit note" };

function tax(figures: Figures | null | undefined): string {
  return figures ? sum([figures.igst, figures.cgst, figures.sgst, figures.cess]) : "";
}

function Bill({ bill }: { bill: BillSide }) {
  return <Link to={`/purchasing/bills/${bill.id}`}>{bill.number}{bill.is_note ? " (debit note)" : ""}</Link>;
}

/** The bills with a 2B line: the same figures, or figures that differ. */
function Pairs({ title, rows, empty }: { title: string; rows: Pair[]; empty: string }) {
  return (
    <section className="statement-section">
      <h2>{title}</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Bill</th><th scope="col">Vendor</th><th scope="col">Their number</th><th scope="col">In the 2B</th>
              <th scope="col" className="k-money">Taxable, booked</th><th scope="col" className="k-money">Taxable, filed</th>
              <th scope="col" className="k-money">Tax, booked</th><th scope="col" className="k-money">Tax, filed</th>
              <th scope="col">Credit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.bill!.id}-${row.line!.id}`}>
                <td><Bill bill={row.bill!} /></td>
                <td>{row.bill!.vendor}</td>
                <td>{row.bill!.reference}</td>
                <td>{row.line!.number} <span className="muted">({row.line!.statement})</span></td>
                <td className="k-money">{money(row.bill!.taxable)}</td>
                <td className="k-money">{money(row.line!.taxable)}</td>
                <td className="k-money">{money(tax(row.bill))}</td>
                <td className="k-money">{money(tax(row.line))}</td>
                <td>{row.line!.itc_available ? "Available" : <span className="bad">Not available{row.line!.reason ? `: ${row.line!.reason}` : ""}</span>}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9} className="muted">{empty}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * The month's GSTR-2B against the bills: credit may be taken only on
 * what the supplier filed. The file is kept as downloaded (JSON, or the
 * B2B and CDNR sheets as CSV) and matched to the year's posted bills
 * each time the page opens, so a bill posted later finds its line.
 */
export default function Gstr2b() {
  const { values, set } = usePeriod(["period"], { period: today().slice(0, 7) });
  const period = values.period!;
  const statements = useGet<Statement[]>(STATEMENTS);
  const report = useGet<Report>(`${STATEMENTS}match/`, { period });
  const act = useAct<Statement & { skipped: string[] }>();
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [skipped, setSkipped] = useState<string[]>([]);
  const kept = (statements.data ?? []).find((row) => row.period === period);

  const read = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { setText(String(reader.result ?? "")); setFileName(file.name); };
    reader.readAsText(file, "utf-8");
  };
  const send = async () => {
    if (kept && !window.confirm(`${period}'s GSTR-2B is already kept (${count(kept.line_count)} lines). Replace it with this file?`)) return;
    const outcome = await act.run("POST", `${STATEMENTS}upload/`, { period, text, replace: Boolean(kept) }, { done: `${period}'s GSTR-2B kept` });
    if (outcome.ok) { setText(""); setFileName(""); setSkipped(outcome.data.skipped); }
  };
  const data = report.data;

  return (
    <section className="report statement">
      <StatementHead title="Input credit against GSTR-2B">
        <label className="inline">Month <input type="month" value={period} onChange={(e) => set("period", e.target.value)} /></label>
      </StatementHead>
      <section className="statement-section">
        <h2>{kept ? `${period}'s 2B is kept: ${count(kept.line_count)} lines, by ${kept.kept_by || "the system"}` : `No GSTR-2B kept for ${period}`}</h2>
        <p className="muted">Download the month's GSTR-2B from the portal as JSON (or its B2B and CDNR sheets as CSV) and keep it here. Nothing is filed and no return changes: what the books claim stays what they say; the waiting figure is what to hold back.</p>
        <div className="field-grid">
          <Field label="The file" hint={fileName ? `${fileName} is loaded` : "The portal's JSON, or a sheet as CSV"}>
            {(id) => <input id={id} type="file" accept=".json,.csv,application/json,text/csv,text/plain" onChange={(event) => read(event.target.files?.[0])} />}
          </Field>
        </div>
        <ActionButton primary pending={act.pending} disabled={!text} onClick={() => void send()}>{kept ? "Replace the kept 2B" : "Keep this 2B"}</ActionButton>
        {skipped.length > 0 && <p className="muted">Not read: {skipped.join("; ")}.</p>}
      </section>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : (
        <>
          {data && data.notes.length > 0 && (
            <div className="note warn" role="note"><ul>{data.notes.map((note) => <li key={note}>{note}</li>)}</ul></div>
          )}
          <div className="tiles">
            <div className="tile"><span>Credit filed this month</span><strong>{data ? money(data.totals.filed) : "…"}</strong><small>In the 2B, available</small></div>
            <div className="tile"><span>Credit booked this month</span><strong>{data ? money(data.totals.booked) : "…"}</strong><small>On the month's bills</small></div>
            <div className="tile"><span>Matched</span><strong>{data ? money(data.totals.matched) : "…"}</strong><small>Booked, and in this month's 2B</small></div>
            <div className={`tile${data && data.totals.waiting !== "0.00" && data.totals.waiting !== "0" ? " bad" : ""}`}>
              <span>Waiting on suppliers</span><strong>{data ? money(data.totals.waiting) : "…"}</strong><small>Booked this year, in no 2B yet</small>
            </div>
            <div className="tile"><span>In the 2B, not booked</span><strong>{data ? money(data.totals.not_booked) : "…"}</strong><small>Filed by suppliers, no bill here</small></div>
          </div>
          {data && (
            <>
              <Pairs title="Matched" rows={data.matched} empty="Nothing matched yet." />
              <Pairs title="Figures differ" rows={data.differs} empty="No bill differs from its 2B line." />
              <section className="statement-section">
                <h2>Waiting on suppliers: booked, in no 2B</h2>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th scope="col">Bill</th><th scope="col">Vendor</th><th scope="col">Their number</th><th scope="col">Date</th><th scope="col" className="k-money">Taxable</th><th scope="col" className="k-money">Tax</th><th scope="col" className="k-money">Value</th></tr></thead>
                    <tbody>
                      {data.not_in_2b.map((row) => (
                        <tr key={row.bill!.id}>
                          <td><Bill bill={row.bill!} /></td><td>{row.bill!.vendor}</td><td>{row.bill!.reference}</td><td>{date(row.bill!.date)}</td>
                          <td className="k-money">{money(row.bill!.taxable)}</td><td className="k-money">{money(tax(row.bill))}</td><td className="k-money">{money(row.bill!.value)}</td>
                        </tr>
                      ))}
                      {data.not_in_2b.length === 0 && <tr><td colSpan={7} className="muted">Every bill of the year has its 2B line.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="statement-section">
                <h2>In the 2B, not booked</h2>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th scope="col">Supplier</th><th scope="col">Number</th><th scope="col">Kind</th><th scope="col">Date</th><th scope="col">2B of</th><th scope="col" className="k-money">Taxable</th><th scope="col" className="k-money">Tax</th><th scope="col" className="k-money">Value</th><th scope="col">Credit</th></tr></thead>
                    <tbody>
                      {data.not_booked.map((row) => (
                        <tr key={row.line!.id}>
                          <td>{row.line!.supplier_name || row.line!.supplier_gstin} <span className="muted">{row.line!.supplier_name ? row.line!.supplier_gstin : ""}</span></td>
                          <td>{row.line!.number}</td><td>{KIND[row.line!.kind] ?? row.line!.kind}</td><td>{date(row.line!.date)}</td><td>{row.line!.statement}</td>
                          <td className="k-money">{money(row.line!.taxable)}</td><td className="k-money">{money(tax(row.line))}</td><td className="k-money">{money(row.line!.value)}</td>
                          <td>{row.line!.itc_available ? "Available" : <span className="bad">Not available{row.line!.reason ? `: ${row.line!.reason}` : ""}</span>}</td>
                        </tr>
                      ))}
                      {data.not_booked.length === 0 && <tr><td colSpan={9} className="muted">Every 2B line has its bill.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </>
      )}
    </section>
  );
}
