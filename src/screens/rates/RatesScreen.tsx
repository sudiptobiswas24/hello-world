import { useId, useMemo } from 'react';
import { todayISO } from '../../engine';
import { eventValue, useStore } from '../../state/store';
import { Card, ClockIcon, PlusIcon, TrashIcon, cx } from '../../components/ui';
import { BulkPanel } from './BulkPanel';
import { RateTrend } from './RateTrend';
import { buildRateSecs, dueBanner, matCats, withMatCat } from './model';
import type { RateSec, RateTone } from './model';
import s from './RatesScreen.module.css';

const RATE_TONE: Record<RateTone, string | undefined> = {
  dirty: s.rateDirty,
  set: undefined,
  missing: s.rateMissing,
};

/** Material rates by section, weekly bulk update, and the week-over-week rate trend. */
export function RatesScreen() {
  const { state } = useStore();
  const today = todayISO();
  const catListId = useId();
  const bulkId = useId();

  const { matSections, rateHistory, rateDraft } = state;
  const secs = useMemo(() => buildRateSecs({ matSections, rateHistory, rateDraft }, today), [matSections, rateHistory, rateDraft, today]);
  const cats = useMemo(() => matCats(matSections), [matSections]);
  const due = dueBanner(rateHistory, today);
  const bulkOpen = !!state.bulkOpen;

  return (
    <div className={s.screen}>
      <datalist id={catListId}>
        {cats.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>

      <DueBanner text={due.text} overdue={due.overdue} bulkOpen={bulkOpen} bulkId={bulkId} />

      {bulkOpen && <BulkPanel id={bulkId} today={today} />}

      {secs.map((sec) => (
        <RateSection key={sec.id} sec={sec} catListId={catListId} />
      ))}

      <RateTrend today={today} />
    </div>
  );
}

function DueBanner({ text, overdue, bulkOpen, bulkId }: { text: string; overdue: boolean; bulkOpen: boolean; bulkId: string }) {
  const { actions } = useStore();
  return (
    <div className={cx(s.full, s.due, overdue ? s.dueWarn : s.dueOk)}>
      <ClockIcon size={16} strokeWidth={1.8} />
      <div className={s.dueText}>{text}</div>
      <button
        type="button"
        className={s.dueBtn}
        aria-expanded={bulkOpen}
        aria-controls={bulkOpen ? bulkId : undefined}
        onClick={() => actions.setState((st) => ({ bulkOpen: !st.bulkOpen }))}
      >
        Update rates
      </button>
    </div>
  );
}

function RateSection({ sec, catListId }: { sec: RateSec; catListId: string }) {
  const { actions } = useStore();
  const headingId = useId();

  const setCat = (itemId: string, cat: string) => actions.setState((st) => ({ matSections: withMatCat(st.matSections, sec.id, itemId, cat) }));
  const setDraft = (itemId: string, v: string) =>
    actions.setState((st) => ({ rateDraft: Object.assign({}, st.rateDraft, { [itemId]: v }) }));

  return (
    <Card>
      <div className={s.secHead}>
        <h2 id={headingId} className={s.title}>
          {sec.label}
        </h2>
        <div className={s.spacer} />
        <button type="button" className={s.addBtn} onClick={() => actions.addMatItem(sec.id)}>
          <PlusIcon size={12} strokeWidth={2} />
          Add material
        </button>
      </div>
      <table className={s.table} aria-labelledby={headingId}>
        <tbody>
          {sec.items.map((m) => {
            const nameFor = m.name || 'material';
            return (
              <tr key={m.id}>
                <th scope="row" className={s.cName}>
                  <input
                    className={cx(s.cellInput, s.nameInput)}
                    value={m.name}
                    aria-label="Material name"
                    onChange={(e) => actions.setMatItem(sec.id, m.id, { name: e.target.value })}
                  />
                </th>
                <td className={s.cCat}>
                  <input
                    className={cx(s.cellInput, s.catInput)}
                    value={m.cat}
                    placeholder="category"
                    list={catListId}
                    aria-label={'Category for ' + nameFor}
                    onChange={(e) => setCat(m.id, e.target.value)}
                  />
                </td>
                <td className={s.cUpd}>{m.lastUpd}</td>
                <td className={s.cUnit}>₹ / kg</td>
                <td className={s.cRate}>
                  <input
                    type="number"
                    inputMode="decimal"
                    min={0}
                    className={cx(s.rateInput, RATE_TONE[m.tone])}
                    value={m.rate}
                    aria-label={'Rate for ' + nameFor + ', ₹ per kg'}
                    onChange={(e) => setDraft(m.id, eventValue(e))}
                  />
                </td>
                <td className={s.cSave}>
                  <button
                    type="button"
                    className={s.saveBtn}
                    title="Save rate"
                    aria-label={'Save rate for ' + nameFor}
                    disabled={!m.dirty}
                    onClick={() => actions.saveMatRate(sec.id, m.id)}
                  >
                    Save
                  </button>
                </td>
                <td className={s.cDel}>
                  <button
                    type="button"
                    className={s.iconBtn}
                    title="Remove material"
                    aria-label={'Remove ' + nameFor}
                    onClick={() => actions.delMatItem(sec.id, m.id)}
                  >
                    <TrashIcon size={13} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
