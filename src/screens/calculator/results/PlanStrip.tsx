import type { CalcResult, Sku } from '../../../engine';
import { cx } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { planStripView } from './model';
import r from './results.module.css';

/** Production days per stage for this order, with the bottleneck highlighted. */
export function PlanStrip({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state } = useStore();
  const v = planStripView(state, sku, result);
  return (
    <div>
      <h3 className={r.heading}>Machine time for this order</h3>
      <table className={r.table}>
        <tbody>
          {v.rows.map((p) => (
            <tr key={p.stage} className={cx(p.flag === 'bottleneck' && r.rowBottleneck, p.flag === 'noCapacity' && r.rowNoCapacity) || undefined}>
              <td className={r.td}>{p.stage}</td>
              <td className={cx(r.td, r.num, r.dim)}>{p.req}</td>
              <td className={cx(r.td, r.num, r.dim)}>{p.cap}</td>
              <td className={cx(r.td, r.num, r.strong)}>{p.days}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={r.planNote}>{v.note}</div>
    </div>
  );
}
