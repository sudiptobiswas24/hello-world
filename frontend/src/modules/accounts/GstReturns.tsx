import { useGet } from "../../api/hooks";
import { today } from "../../forms/fields";
import { count, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { StatementHead, usePeriod } from "./Statement";

type Heads = { igst: string; cgst: string; sgst: string; cess: string; taxable?: string };

interface Gstr1 {
  gstin: string;
  warnings: string[];
  not_built: string[];
  [section: string]: unknown;
}

interface Gstr3b {
  gstin: string;
  "3.1a": Heads; "3.1b": Heads; "3.1c": Heads; "3.1e": Heads;
  "4A5": Heads;
  "5": { inter: string; intra: string };
  warnings: string[];
}

const SECTIONS: [string, string][] = [
  ["b2b", "B2B invoices"], ["b2cl", "B2C large"], ["b2cs", "B2C small"], ["exp", "Exports"],
  ["cdnr", "Credit/debit notes (registered)"], ["cdnur", "Credit/debit notes (unregistered)"],
  ["hsn_b2b", "HSN summary, B2B"], ["hsn_b2c", "HSN summary, B2C"], ["documents", "Documents issued"],
];

const ROWS_3B: [keyof Gstr3b, string][] = [
  ["3.1a", "3.1(a) Outward taxable"], ["3.1b", "3.1(b) Zero rated"], ["3.1c", "3.1(c) Nil and exempt"],
  ["3.1e", "3.1(e) Non-GST"], ["4A5", "4(A)(5) ITC, all other"],
];

/**
 * The month's returns as the books make them: compiled from what was
 * posted, nothing stored and nothing filed. GSTR-1 downloads in the
 * offline tool's shape, to validate there before uploading.
 */
export default function GstReturns() {
  const { values, set } = usePeriod(["period"], { period: today().slice(0, 7) });
  const period = values.period!;
  const one = useGet<Gstr1>("/api/gst/gstr1/", { period });
  const three = useGet<Gstr3b>("/api/gst/gstr3b/", { period });
  const warnings = [...new Set([...(one.data?.warnings ?? []), ...(three.data?.warnings ?? [])])];
  return (
    <section className="report statement">
      <StatementHead title="GST returns">
        <label className="inline">Month <input type="month" value={period} onChange={(e) => set("period", e.target.value)} /></label>
        <a className="btn" href={`/api/gst/gstr1/json/?period=${period}`} download={`GSTR1-${period}.json`}>GSTR-1 JSON</a>
      </StatementHead>
      {warnings.length > 0 && (
        <div className="note warn" role="note">
          <strong>Look at these before filing.</strong>
          <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      )}
      {one.isError ? <ErrorPanel error={one.error} retry={() => void one.refetch()} /> : (
        <section className="statement-section">
          <h2>GSTR-1 · {one.data?.gstin || "no GSTIN set"}</h2>
          <table>
            <tbody>
              {SECTIONS.map(([key, label]) => (
                <tr key={key}><td>{label}</td><td className="k-quantity">{one.data ? count(((one.data[key] as unknown[]) ?? []).length) : "…"}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {three.isError ? <ErrorPanel error={three.error} retry={() => void three.refetch()} /> : (
        <section className="statement-section">
          <h2>GSTR-3B</h2>
          <table>
            <thead><tr><th scope="col" /><th scope="col" className="k-money">Taxable</th><th scope="col" className="k-money">IGST</th><th scope="col" className="k-money">CGST</th><th scope="col" className="k-money">SGST</th><th scope="col" className="k-money">Cess</th></tr></thead>
            <tbody>
              {ROWS_3B.map(([key, label]) => {
                const heads = three.data?.[key] as Heads | undefined;
                return (
                  <tr key={String(key)}>
                    <td>{label}</td>
                    <td className="k-money">{heads?.taxable !== undefined ? money(heads.taxable) : ""}</td>
                    <td className="k-money">{money(heads?.igst)}</td><td className="k-money">{money(heads?.cgst)}</td>
                    <td className="k-money">{money(heads?.sgst)}</td><td className="k-money">{money(heads?.cess)}</td>
                  </tr>
                );
              })}
              <tr><td>5 Exempt, nil and non-GST inward</td><td className="k-money" colSpan={5}>
                {three.data ? `${money(three.data["5"].inter)} inter-state · ${money(three.data["5"].intra)} within the state` : "…"}</td></tr>
            </tbody>
          </table>
        </section>
      )}
    </section>
  );
}
