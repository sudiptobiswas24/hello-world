import { useMemo } from 'react';
import { num, qcModel } from '../../engine';
import type { Sku } from '../../engine';
import { useStore } from '../../state/store';
import { Field, NumberInput, SectionTitle, TextInput, cx } from '../../components/ui';
import { Group } from './FormBits';
import type { SpecProps } from './SpecForm';
import { blendShares, qcElPred, qcModelNote, qcRecord, qcStrPred, qtyKg, reqTone } from './model';
import f from './SpecForm.module.css';

/** Bale size, strength/elongation with QC predictions, tape blend shares, quantity, margin. */
export function OrderQcSection({ sku, result: r }: SpecProps) {
  const { state, actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  const qcLog = state.qcLog;
  const M = useMemo(() => qcModel({ qcLog }), [qcLog]);
  const shares = blendShares(sku.tapeMix);
  const canLog = num(sku.strength) > 0 || num(sku.elong) > 0;
  return (
    <div>
      <SectionTitle>Order &amp; QC</SectionTitle>
      <div className={cx(f.grid, f.g110)}>
        <Field label="Bags / bale">{(id) => <NumberInput id={id} value={sku.bagsPerBale} placeholder="e.g. 500" onChange={bind('bagsPerBale')} />}</Field>
        <Field label="Strength">
          {(id) => (
            <>
              <TextInput id={id} value={sku.strength} onChange={bind('strength')} placeholder="e.g. 5 gpd" />
              <div title="Predicted by the QC model (baseline tests + your logged results); tape strength kg = gpd × denier ÷ 1000" className={f.pred}>
                {qcStrPred(sku, r, M)}
              </div>
            </>
          )}
        </Field>
        <Field label="Elongation">
          {(id) => (
            <>
              <TextInput id={id} value={sku.elong} onChange={bind('elong')} placeholder="e.g. 18–22%" />
              <div title="Predicted by the QC model (baseline tests + your logged results)" className={f.pred}>
                {qcElPred(sku, M)}
              </div>
            </>
          )}
        </Field>
        <Group label="Tape blend used" className={f.span2}>
          <div title="Share of each material in the tape mix by weight — the CC% here drives the strength and elongation predictions" className={f.blendBox}>
            <span className={f.strong}>PP {shares.pp}</span>
            <span className={cx(f.strong, shares.ccTone === 'warn' ? f.toneWarn : f.toneOk)}>CC {shares.cc}</span>
            <span>MB {shares.mb}</span>
            <span>LLDPE {shares.lldpe}</span>
            <span className={f.blendOther}>{shares.other}</span>
          </div>
        </Group>
        <div className={cx(f.full, f.modelRow)}>
          <span className={f.modelNote}>{qcModelNote(M)}</span>
          {canLog && (
            <button
              type="button"
              className={f.tinyBtn}
              title="Adds this bag's measured strength/elongation with its CC% and denier to the prediction model"
              onClick={() => actions.setState((s) => ({ qcLog: (s.qcLog || []).concat([qcRecord(sku, r)]) }))}
            >
              Log test to model
            </button>
          )}
        </div>
        <Field label="Qty pcs">
          {(id) => <NumberInput id={id} value={sku.qty} placeholder="e.g. 50000" onChange={bind('qty')} tone={reqTone(num(sku.qty) > 0)} />}
        </Field>
        <Field label="Qty kg">
          {(id) => (
            <NumberInput
              id={id}
              value={qtyKg(sku, r)}
              placeholder="e.g. 3400"
              title="Editing kg recomputes pcs from bag weight"
              onChange={(e) => {
                const kg = num(e.target.value);
                if (r.totalG > 0) actions.updateSku(sku.id, { qty: Math.max(1, Math.round((kg * 1000) / r.totalG)) });
              }}
            />
          )}
        </Field>
        <Field label="Margin %">
          {(id) => <NumberInput id={id} value={sku.marginOv} onChange={bind('marginOv')} placeholder={String(state.glob.margin)} />}
        </Field>
      </div>
    </div>
  );
}
