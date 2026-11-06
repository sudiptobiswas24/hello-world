import { useState } from "react";

import { useAct, useGet, useRows } from "../../api/hooks";
import { money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Block {
  block: number; block_code: string; block_name: string; rate_percent: string | null; rate_confirmed: boolean;
  opening: string; additions_full: string; additions_half: string; sales: string; depreciation: string;
  closing: string; capital_gain: string; ceased: boolean; book_depreciation: string; difference: string;
  book_value: string; timing_difference: string;
}
interface Schedule {
  year_start: string; year_end: string; filed: { id: number; filed_on: string } | null; blocks: Block[];
  totals: { depreciation: string; book_depreciation: string; difference: string };
}

interface Rate {
  id: number; section: number; section_code: string; applies_from: string; rate_percent: string; authority: string;
  confirmed_on: string | null;
}
interface Opening { id: number; block: number; block_code: string; year_start: string; written_down_value: string }
interface TaxBlock { id: number; code: string; name: string }

/** The blocks' rates as dated rows, each confirmed by the accountant before a year is filed at it. */
function Rates() {
  const act = useAct();
  const rates = useRows<Rate>("/api/assets/tax-block-rates/", {});
  if (rates.isError) return <ErrorPanel error={rates.error} retry={() => void rates.refetch()} />;
  return (
    <div className="table-wrap">
      <table aria-label="Rates">
        <thead><tr><th scope="col">Block</th><th scope="col">From</th><th scope="col" className="k-money">Rate</th>
          <th scope="col">Authority</th><th scope="col">Confirmed</th></tr></thead>
        <tbody>
          {(rates.data ?? []).map((rate) => (
            <tr key={rate.id}>
              <td>{rate.section_code}</td><td>{rate.applies_from}</td><td className="k-money">{rate.rate_percent}%</td>
              <td>{rate.authority}</td>
              <td>{rate.confirmed_on ?? (
                <button type="button" disabled={act.pending}
                  onClick={() => void act.run("POST", `/api/assets/tax-block-rates/${rate.id}/confirm/`,
                    { section: rate.section, applies_from: rate.applies_from, rate_percent: rate.rate_percent },
                    { done: `${rate.section_code} from ${rate.applies_from} is confirmed.` })}>
                  Confirm {rate.section_code} from {rate.applies_from}</button>)}</td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}

/** Each block's written-down value from the last return, where the system's schedule starts. */
function Openings() {
  const act = useAct();
  const openings = useRows<Opening>("/api/assets/tax-block-openings/", {});
  const blocks = useRows<TaxBlock>("/api/assets/tax-blocks/", {});
  const [form, setForm] = useState({ block: "", year_start: "", written_down_value: "" });
  if (openings.isError) return <ErrorPanel error={openings.error} retry={() => void openings.refetch()} />;
  return (
    <>
      <div className="table-wrap">
        <table aria-label="Openings">
          <thead><tr><th scope="col">Block</th><th scope="col">Tax year from</th><th scope="col" className="k-money">Written-down value</th></tr></thead>
          <tbody>
            {(openings.data ?? []).map((row) => (
              <tr key={row.id}><td>{row.block_code}</td><td>{row.year_start}</td><td className="k-money">{money(row.written_down_value)}</td></tr>))}
          </tbody>
        </table>
      </div>
      <form aria-label="Add an opening" onSubmit={(event) => {
        event.preventDefault();
        void act.run("POST", "/api/assets/tax-block-openings/", form, { done: "Opening added.",
          onDone: () => setForm({ block: "", year_start: "", written_down_value: "" }) });
      }}>
        <label>Block <select value={form.block} onChange={(e) => setForm({ ...form, block: e.target.value })}>
          <option value="">Choose</option>
          {(blocks.data ?? []).map((block) => <option key={block.id} value={block.id}>{block.code} {block.name}</option>)}
        </select></label>
        <label>Opens <input type="date" value={form.year_start} onChange={(e) => setForm({ ...form, year_start: e.target.value })} /></label>
        <label>Written-down value <input inputMode="decimal" value={form.written_down_value}
          onChange={(e) => setForm({ ...form, written_down_value: e.target.value })} /></label>
        <button type="submit" disabled={act.pending}>Add the opening</button>
      </form>
    </>
  );
}

/**
 * The Income-tax depreciation schedule by block of assets (assets.tax), beside the books' own depreciation:
 * never posted. Filed, a year is kept as filed.
 */
export default function TaxDepreciation() {
  const [year, setYear] = useState("");
  const act = useAct();
  const report = useGet<Schedule>("/api/assets/tax-years/schedule/", { year }, Boolean(year));
  const data = report.data;
  return (
    <section className="report">
      <header className="page-head">
        <h1>Tax depreciation by block</h1>
        <label className="inline">Tax year from <input type="date" aria-label="Tax year from" value={year} onChange={(e) => setYear(e.target.value)} /></label>
        {data && (data.filed
          ? <span className="pill pill-done">Filed on {data.filed.filed_on}</span>
          : <button type="button" disabled={act.pending}
            onClick={() => void act.run("POST", "/api/assets/tax-years/file/", { year: data.year_start },
              { done: `The tax year from ${data.year_start} is filed.` })}>File the year</button>)}
      </header>
      {report.isError ? <ErrorPanel error={report.error} retry={() => void report.refetch()} /> : data && (
        <div className="table-wrap">
          <table aria-label="Blocks">
            <thead><tr>
              <th scope="col">Block</th><th scope="col" className="k-money">Rate</th><th scope="col" className="k-money">Opening</th>
              <th scope="col" className="k-money">Added, full rate</th><th scope="col" className="k-money">Added, half rate</th>
              <th scope="col" className="k-money">Sales</th><th scope="col" className="k-money">Depreciation</th>
              <th scope="col" className="k-money">Closing</th><th scope="col" className="k-money">Capital gain</th>
              <th scope="col" className="k-money">Book depreciation</th><th scope="col" className="k-money">Difference</th>
              <th scope="col" className="k-money">Tax less book value</th>
            </tr></thead>
            <tbody>
              {data.blocks.length === 0 ? <tr><td colSpan={12}>No block has figures for this year.</td></tr> : data.blocks.map((row) => (
                <tr key={row.block}>
                  <td>{row.block_code} {row.block_name}{row.ceased ? " (ceased)" : ""}</td>
                  <td className="k-money">{row.rate_percent ?? "none"}%{row.rate_confirmed ? "" : " unconfirmed"}</td>
                  <td className="k-money">{money(row.opening)}</td><td className="k-money">{money(row.additions_full)}</td>
                  <td className="k-money">{money(row.additions_half)}</td><td className="k-money">{money(row.sales)}</td>
                  <td className="k-money">{money(row.depreciation)}</td><td className="k-money">{money(row.closing)}</td>
                  <td className="k-money">{money(row.capital_gain)}</td><td className="k-money">{money(row.book_depreciation)}</td>
                  <td className="k-money">{money(row.difference)}</td><td className="k-money">{money(row.timing_difference)}</td>
                </tr>))}
            </tbody>
            <tfoot><tr><th scope="row" colSpan={6}>Total</th><td className="k-money">{money(data.totals.depreciation)}</td>
              <td colSpan={2} /><td className="k-money">{money(data.totals.book_depreciation)}</td>
              <td className="k-money">{money(data.totals.difference)}</td><td /></tr></tfoot>
          </table>
        </div>
      )}
      <h2>Rates</h2>
      <Rates />
      <h2>Openings</h2>
      <Openings />
    </section>
  );
}
