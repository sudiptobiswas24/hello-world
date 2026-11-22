import { useMemo } from 'react';
import { inr, mixAnalysis, mixBlend, num } from '../../engine';
import type { FindingTone, MixAnalysis, MixRow } from '../../engine';
import { useStore } from '../../state/store';
import type { MixKey } from '../../state/store';
import { NumberInput, SectionTitle, Select, cx } from '../../components/ui';
import type { SpecProps } from './SpecForm';
import { ashHint, ashMix, mixRowsView } from './model';
import m from './MixSection.module.css';

const DOT_CLASS: Record<FindingTone, string> = { error: m.dotError, warn: m.dotWarn, info: m.dotInfo, good: m.dotGood };
const GRADE_CLASS: Record<MixAnalysis['grade'], string> = { standard: m.gradeStandard, local: m.gradeLocal, none: m.gradeNone };

/** Material mix (relative weights): tape blend with analysis, and the lamination blend. */
export function MixSection({ sku, result: r }: SpecProps) {
  const { state, actions } = useStore();
  const { matSections, rateHistory, qcLog } = state;
  const MA = useMemo(() => mixAnalysis({ matSections, rateHistory, qcLog }, sku, r), [matSections, rateHistory, qcLog, sku, r]);
  const fixed = ashMix(sku);
  return (
    <div>
      <SectionTitle>Material mix (relative weights)</SectionTitle>
      <div className={m.grid}>
        <div>
          <MixHead title="Tape plant" blend={inr(mixBlend(state, sku.tapeMix))} onAdd={() => actions.addMixRow(sku.id, 'tapeMix', 'tape')} />
          {num(sku.ashPct) > 0 && (
            <div className={m.hintBox}>
              <span className={m.grow}>{ashHint(sku)}</span>
              <button type="button" className={m.applyBtn} onClick={() => fixed && actions.setMix(sku.id, 'tapeMix', fixed)}>
                Apply
              </button>
            </div>
          )}
          <MixTable skuId={sku.id} mixKey="tapeMix" secId="tape" mix={sku.tapeMix} />
          <div className={m.analysis}>
            <div className={m.anaHead}>
              <span className={m.anaTitle}>Mix analysis</span>
              <span className={cx(m.grade, GRADE_CLASS[MA.grade])}>{MA.mixGrade}</span>
              <span className={m.grow} />
              <span title="Predictions from your tested-bag data: gpd = 5.74 − 0.033 × CC%; elongation = 24.8 − 0.144 × CC%" className={m.stats}>
                {MA.mixStats}
              </span>
            </div>
            <ul className={m.findings}>
              {MA.findings.map((fd, i) => (
                <li key={i} className={m.finding}>
                  <span aria-hidden="true" className={cx(m.dot, DOT_CLASS[fd.tone])} />
                  <span className={m.findingMsg}>{fd.msg}</span>
                  {fd.fixLabel && fd.fixMix && (
                    <button type="button" className={m.fixBtn} onClick={() => fd.fixMix && actions.setMix(sku.id, 'tapeMix', fd.fixMix)}>
                      {fd.fixLabel}
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {MA.hasRec && (
              <div className={cx(m.hintBox, m.recBox)}>
                <span className={m.grow}>{MA.recDesc}</span>
                <button type="button" className={m.applyBtn} onClick={() => actions.setMix(sku.id, 'tapeMix', MA.recMix)}>
                  Apply mix
                </button>
              </div>
            )}
          </div>
        </div>
        {sku.lamSide !== 'none' && (
          <div>
            <MixHead title="Lamination" blend={inr(mixBlend(state, sku.lamMix))} onAdd={() => actions.addMixRow(sku.id, 'lamMix', 'lam')} />
            <MixTable skuId={sku.id} mixKey="lamMix" secId="lam" mix={sku.lamMix} />
          </div>
        )}
      </div>
    </div>
  );
}

function MixHead({ title, blend, onAdd }: { title: string; blend: string; onAdd: () => void }) {
  return (
    <div className={m.head}>
      <h4 className={m.headTitle}>{title}</h4>
      <div className={m.grow} />
      <div className={m.blend}>Blend {blend} / kg</div>
      <button type="button" className={m.addBtn} onClick={onAdd}>
        + Add
      </button>
    </div>
  );
}

function MixTable({ skuId, mixKey, secId, mix }: { skuId: string; mixKey: MixKey; secId: string; mix: MixRow[] | undefined }) {
  const { state, actions } = useStore();
  const { opts, rows } = mixRowsView(state, mix, secId);
  return (
    <table className={m.table}>
      <tbody>
        {rows.map((x, i) => (
          <tr key={i}>
            <td className={m.tdSel}>
              <Select aria-label="Material" value={x.m} onChange={(e) => actions.updMixRow(skuId, mixKey, i, { m: e.target.value })}>
                {opts.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
            </td>
            <td className={m.tdWt}>
              <NumberInput aria-label="Weight" min={undefined} value={x.wt} onChange={(e) => actions.updMixRow(skuId, mixKey, i, { wt: e.target.value })} placeholder="wt" />
            </td>
            <td className={m.tdPct}>{x.pct}</td>
            <td className={m.tdDel}>
              <button type="button" className={m.delBtn} title="Remove" aria-label="Remove" onClick={() => actions.delMixRow(skuId, mixKey, i)}>
                ×
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
