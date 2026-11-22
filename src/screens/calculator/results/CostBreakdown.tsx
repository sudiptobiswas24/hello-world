import type { AppState, CalcResult } from '../../../engine';
import { cx } from '../../../components/ui';
import { costView } from './model';
import r from './results.module.css';
import s from './CostBreakdown.module.css';

/** Material consumption (BOM), conversion cost and the cost → quote → order totals. */
export function CostBreakdown({ result, glob }: { result: CalcResult; glob: AppState['glob'] }) {
  const v = costView(result, glob);
  return (
    <>
      <div>
        <h3 className={r.heading}>Material consumption &amp; cost</h3>
        <table className={r.table}>
          <thead>
            <tr>
              <th scope="col" className={r.th}>Component</th>
              <th scope="col" className={cx(r.th, r.thNum)}>g/bag</th>
              <th scope="col" className={cx(r.th, r.thNum)}>kg/order</th>
              <th scope="col" className={cx(r.th, r.thNum)}>Rate ₹/kg</th>
              <th scope="col" className={cx(r.th, r.thNum)}>₹/bag</th>
            </tr>
          </thead>
          <tbody>
            {v.bom.map((b, i) => (
              <tr key={i + b.name}>
                <td className={r.td}>{b.name}</td>
                <td className={cx(r.td, r.num)}>{b.g}</td>
                <td className={cx(r.td, r.num)}>{b.kg}</td>
                <td className={cx(r.td, r.num, r.dim)}>{b.rate}</td>
                <td className={cx(r.td, r.num, r.strong)}>{b.cost}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h3 className={r.heading}>Conversion cost</h3>
        <table className={r.table}>
          <tbody>
            {v.conv.map((c, i) => (
              <tr key={i + c.name}>
                <td className={r.td}>{c.name}</td>
                <td className={cx(r.td, r.num, r.dim)}>{c.basis}</td>
                <td className={cx(r.td, r.num, r.strong, r.costCol)}>{c.cost}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={s.totals}>
        <div className={s.row}>
          <span className={s.label}>Material</span>
          <span>{v.matS}</span>
        </div>
        <div className={s.row}>
          <span className={s.label}>Conversion</span>
          <span>{v.convTS}</span>
        </div>
        <div className={cx(s.row, s.ruled)}>
          <span className={s.label}>Overhead ({v.ovhPctS}%)</span>
          <span>{v.ovhS}</span>
        </div>
        <div className={cx(s.row, s.afterRule, s.strong)}>
          <span>Cost per bag</span>
          <span>{v.costBagS}</span>
        </div>
        <div className={cx(s.row, s.dim)}>
          <span>Cost per kg</span>
          <span>{v.costKgS}</span>
        </div>
        <div className={cx(s.row, s.dim, s.ruled)}>
          <span>Cost per meter (fabric)</span>
          <span>{v.costMS}</span>
        </div>
        <div className={cx(s.row, s.afterRule)}>
          <span className={s.label}>Margin ({v.marginPctS}%)</span>
          <span className={v.marginNegative ? s.marginNeg : s.marginOk}>{v.marginS}</span>
        </div>
        <div className={cx(s.row, s.quote)}>
          <span>Quote per bag</span>
          <span className={s.quoteValue}>{v.quoteS}</span>
        </div>
        <div className={cx(s.row, s.dim)}>
          <span>
            Order value ({v.qtyS} bags · {v.qtyKgS} kg)
          </span>
          <span>{v.orderValS}</span>
        </div>
        <div className={cx(s.row, s.dim)}>
          <span>GST ({v.gstPctS}%)</span>
          <span>{v.gstS}</span>
        </div>
        <div className={cx(s.row, s.grand)}>
          <span>Order total incl. GST</span>
          <span>{v.grandS}</span>
        </div>
      </div>
    </>
  );
}
