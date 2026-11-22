import { useId, useMemo } from 'react';
import { useStore } from '../../state/store';
import { Card, cx } from '../../components/ui';
import { buildWeekRows, buildWowRows, histOptions } from './model';
import { DELTA_TONE } from './tones';
import s from './RatesScreen.module.css';

/** Week-over-week rate table: every material's latest week, or one material's weekly history. */
export function RateTrend({ today }: { today: string }) {
  const { state, actions } = useStore();
  const headingId = useId();
  const { matSections, rateHistory } = state;
  const histSel = state.histSel || 'all';
  const opts = useMemo(() => histOptions(matSections), [matSections]);
  const trendAll = histSel === 'all';
  const trendOne = !trendAll;
  const wowRows = useMemo(() => (trendAll ? buildWowRows({ matSections, rateHistory }, today) : []), [trendAll, matSections, rateHistory, today]);
  const weekRows = useMemo(() => (trendOne ? buildWeekRows({ rateHistory }, histSel) : []), [trendOne, rateHistory, histSel]);
  const hasHist = (rateHistory || []).length > 0;

  return (
    <Card className={s.full}>
      <div className={s.trendHead}>
        <h2 id={headingId} className={s.title}>
          Rate trend (week-over-week)
        </h2>
        <select
          className={s.histSelect}
          value={histSel}
          aria-label="Material for rate trend"
          onChange={(e) => actions.setState({ histSel: e.target.value })}
        >
          {opts.map((o, i) => (
            <option key={i + ':' + o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <div className={s.spacer} />
        <div className={s.trendNote}>Bucketed by week · latest rate in each week</div>
      </div>

      {trendAll && (
        <table className={s.table} aria-labelledby={headingId}>
          <thead>
            <tr>
              <th scope="col" className={cx(s.th, s.first)}>
                Material
              </th>
              <th scope="col" className={s.th}>
                Current rate
              </th>
              <th scope="col" className={s.th}>
                Latest week
              </th>
              <th scope="col" className={cx(s.th, s.last)}>
                Δ vs prev week
              </th>
            </tr>
          </thead>
          <tbody>
            {wowRows.map((h) => (
              <tr key={h.id}>
                <th scope="row" className={cx(s.td, s.first)}>
                  {h.name}
                </th>
                <td className={cx(s.td, s.num, s.rateVal)}>{h.rate}</td>
                <td className={cx(s.td, s.num, s.metaText)}>{h.week}</td>
                <td className={cx(s.td, s.last, s.num)}>
                  <span className={DELTA_TONE[h.delta.tone]}>{h.delta.text}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {trendOne && (
        <table className={s.table} aria-labelledby={headingId}>
          <thead>
            <tr>
              <th scope="col" className={cx(s.th, s.first)}>
                Week of
              </th>
              <th scope="col" className={s.th}>
                Rate
              </th>
              <th scope="col" className={cx(s.th, s.last)}>
                Δ vs prev week
              </th>
            </tr>
          </thead>
          <tbody>
            {weekRows.map((h) => (
              <tr key={h.id}>
                <th scope="row" className={cx(s.td, s.first, s.weekCell)}>
                  {h.week}
                </th>
                <td className={cx(s.td, s.num, s.rateVal)}>{h.rate}</td>
                <td className={cx(s.td, s.last, s.num)}>
                  <span className={DELTA_TONE[h.delta.tone]}>{h.delta.text}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!hasHist && <div className={s.empty}>Change any rate above to log the first entry.</div>}
    </Card>
  );
}
