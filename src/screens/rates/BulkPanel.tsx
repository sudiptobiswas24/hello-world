import { useId, useMemo } from 'react';
import { eventValue, useStore } from '../../state/store';
import { CloseIcon, cx } from '../../components/ui';
import { buildBulk, bulkFlashText } from './model';
import { DELTA_TONE } from './tones';
import s from './RatesScreen.module.css';

/** Weekly bulk rate update: enter only changed rates, one effective date for the batch. */
export function BulkPanel({ id, today }: { id: string; today: string }) {
  const { state, actions } = useStore();
  const headingId = useId();
  const { matSections, rateHistory, bulkEff, bulkDraft } = state;
  const vm = useMemo(
    () => buildBulk({ matSections, rateHistory, bulkEff, bulkDraft }, today),
    [matSections, rateHistory, bulkEff, bulkDraft, today],
  );
  const flash = bulkFlashText(state.bulkFlash || 0);

  const setDraft = (itemId: string, v: string) =>
    actions.setState((st) => ({ bulkDraft: Object.assign({}, st.bulkDraft, { [itemId]: v }) }));

  return (
    <section id={id} aria-labelledby={headingId} className={cx(s.full, s.bulk)}>
      <div className={s.bulkHead}>
        <h2 id={headingId} className={s.title}>
          Bulk rate update
        </h2>
        <div className={s.bulkSub}>Enter only the rates that changed · one effective date for the batch</div>
        <div className={s.spacer} />
        <label className={s.effLabel}>
          Effective from{' '}
          <input type="date" className={s.dateInput} value={vm.eff} onChange={(e) => actions.setState({ bulkEff: e.target.value })} />
        </label>
        <button
          type="button"
          className={s.iconBtn}
          title="Close"
          aria-label="Close"
          onClick={() => actions.setState((st) => ({ bulkOpen: !st.bulkOpen }))}
        >
          <CloseIcon size={16} strokeWidth={2} />
        </button>
      </div>

      {vm.future && (
        <div className={s.futureNote}>
          Post-dated — these rates are scheduled and will apply to costing only from the effective date. Current costing is unchanged until then.
        </div>
      )}

      <div className={s.bulkGrid}>
        {vm.secs.map((bs) => (
          <div key={bs.id} className={s.bulkSec}>
            <h3 className={s.secLabel}>{bs.label}</h3>
            <table className={s.table}>
              <tbody>
                {bs.items.map((bm) => (
                  <tr key={bm.id}>
                    <th scope="row" className={s.bName}>
                      {bm.name}
                    </th>
                    <td className={s.bCur}>{bm.cur}</td>
                    <td className={s.bInpCell}>
                      <input
                        type="number"
                        inputMode="decimal"
                        min={0}
                        className={s.bInput}
                        value={bm.draft}
                        placeholder="new"
                        aria-label={'New rate for ' + (bm.name || 'material')}
                        onChange={(e) => setDraft(bm.id, eventValue(e))}
                      />
                    </td>
                    <td className={s.bDelta}>
                      <span className={DELTA_TONE[bm.delta.tone]}>{bm.delta.text}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <div className={s.bulkFoot}>
        <button type="button" className={s.saveAll} onClick={() => actions.bulkSave()}>
          Save {vm.count} change(s)
        </button>
        <button type="button" className={s.clearBtn} onClick={() => actions.setState({ bulkDraft: {} })}>
          Clear
        </button>
        <div className={s.flash} role="status">
          {flash}
        </div>
      </div>
    </section>
  );
}
