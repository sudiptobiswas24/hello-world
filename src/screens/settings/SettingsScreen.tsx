import { useId } from 'react';
import { num } from '../../engine';
import type { ConversionRates, GlobalSettings, NumInput, WastageAllowances } from '../../engine';
import { eventValue, useStore } from '../../state/store';
import { Card, cx } from '../../components/ui';
import s from './SettingsScreen.module.css';

const CONV_DEFS: [keyof ConversionRates, string, string][] = [
  ['tape', 'Tape extrusion', '₹ / kg'],
  ['weaving', 'Weaving', '₹ / kg'],
  ['lamination', 'Lamination', '₹ / kg'],
  ['printing', 'Printing', '₹ / kg'],
  ['cutting', 'Cutting + stitching', '₹ / kg'],
  ['linerins', 'Liner insertion', '₹ / bag'],
  ['dcut', 'D-cut punching', '₹ / bag'],
  ['handleAt', 'Handle attachment', '₹ / bag'],
  ['valveAt', 'Valve patch fixing', '₹ / bag'],
  ['packing', 'Bale packing', '₹ / bag'],
];

const WAST_DEFS: [keyof WastageAllowances, string, string][] = [
  ['fabAllow', 'Fabric meters allowance', '%'],
  ['lamAllow', 'Lam meters allowance', '%'],
  ['boppAllow', 'BOPP meters allowance', '%'],
];

const GLOB_DEFS: [keyof GlobalSettings, string, string][] = [
  ['overhead', 'Overhead', '%'],
  ['margin', 'Default margin', '%'],
  ['gst', 'GST', '%'],
  ['inkFactor', 'Ink g/m² per color', 'g'],
  ['stitchDm', 'Stitches per dm', 'st'],
  ['threadFactor', 'Thread factor (× seam length)', '×'],
  ['redPct', 'Reducer (% of ink)', '%'],
  ['mibkPct', 'MIBK (% of ink)', '%'],
  ['workHours', 'Working hours per day', 'h'],
  ['inkCoverage', 'Ink coverage', '%'],
  ['roundM', 'Order meters round-up increment', 'm'],
];

/** Conversion costs, meter allowances and global costing factors. */
export function SettingsScreen() {
  const { state, actions } = useStore();
  const uid = useId();
  const { convs, wast, glob } = state;

  return (
    <div className={s.screen}>
      <Card>
        <h2 className={s.head}>Process conversion costs</h2>
        <table className={s.table}>
          <tbody>
            {CONV_DEFS.map(([k, label, basis]) => {
              const id = uid + 'conv-' + k;
              return (
                <tr key={k}>
                  <th scope="row" className={s.label}>
                    <label htmlFor={id}>{label}</label>
                  </th>
                  <td className={s.basis}>{basis}</td>
                  <td className={cx(s.val, s.valConv)}>
                    <NumField
                      id={id}
                      className={cx(s.inputConv, !(num(convs[k]) > 0) && s.missing)}
                      value={convs[k]}
                      onValue={(v) => actions.setState((st) => ({ convs: Object.assign({}, st.convs, { [k]: v }) }))}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card>
        <h2 className={s.head}>Allowances &amp; lamination factors</h2>
        <table className={s.table}>
          <tbody>
            {WAST_DEFS.map(([k, label, suffix]) => {
              const id = uid + 'wast-' + k;
              return (
                <tr key={k}>
                  <th scope="row" className={s.label}>
                    <label htmlFor={id}>{label}</label>
                  </th>
                  <td className={cx(s.val, s.valWast)}>
                    <NumField
                      id={id}
                      className={s.inputSmall}
                      value={wast[k]}
                      onValue={(v) => actions.setState((st) => ({ wast: Object.assign({}, st.wast, { [k]: v }) }))}
                    />{' '}
                    <span className={s.suffix}>{suffix}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card>
        <h2 className={s.head}>Globals</h2>
        <table className={s.table}>
          <tbody>
            {GLOB_DEFS.map(([k, label, suffix]) => {
              const id = uid + 'glob-' + k;
              return (
                <tr key={k}>
                  <th scope="row" className={s.label}>
                    <label htmlFor={id}>{label}</label>
                  </th>
                  <td className={cx(s.val, s.valGlob)}>
                    <NumField
                      id={id}
                      className={s.inputSmall}
                      value={glob[k]}
                      onValue={(v) => actions.setState((st) => ({ glob: Object.assign({}, st.glob, { [k]: v }) }))}
                    />{' '}
                    <span className={s.suffix}>{suffix}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function NumField({ id, className, value, onValue }: { id: string; className?: string; value: NumInput | undefined; onValue: (v: string) => void }) {
  return (
    <input
      id={id}
      type="number"
      inputMode="decimal"
      min={0}
      className={cx(s.input, className)}
      value={value ?? ''}
      onChange={(e) => onValue(eventValue(e))}
    />
  );
}
