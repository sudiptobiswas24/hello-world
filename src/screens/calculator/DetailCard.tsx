import { useEffect, useRef, useState } from 'react';
import type { CalcResult, Sku } from '../../engine';
import { useStore } from '../../state/store';
import { Card, LockIcon, PrintIcon, cx } from '../../components/ui';
import { DetailResults } from './DetailResults';
import { DimensionsRow, FabricRow, JobRow } from './SpecForm';
import { AddonsSection, ExtrasSection, FilmsSection } from './SpecOptions';
import { OrderQcSection } from './OrderQcSection';
import { MixSection } from './MixSection';
import { ImagesSection } from './ImagesSection';
import { dimsEcho } from './model';
import f from './SpecForm.module.css';
import s from './DetailCard.module.css';

const SAVED_FLASH_MS = 1600;

/** Cost sheet for the selected bag: header actions, issues, spec form, then results. */
export function DetailCard({ sku, result: r }: { sku: Sku; result: CalcResult }) {
  const { actions } = useStore();
  const locked = !!sku.locked;
  const [savedFlash, setSavedFlash] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout>>();
  const bodyRef = useRef<HTMLDivElement>(null);

  // a locked spec is read-only: no pointer, keyboard or assistive-tech edits
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.inert = locked;
  }, [locked]);
  useEffect(() => () => clearTimeout(flashTimer.current), []);

  const saved = savedFlash === sku.id;
  const saveBag = () => {
    actions.setState({}); // queue an immediate save of the current state
    setSavedFlash(sku.id);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setSavedFlash(null), SAVED_FLASH_MS);
  };

  return (
    <Card>
      <div className={s.header}>
        <input aria-label="Bag name" className={s.nameInput} value={sku.name} onChange={actions.bindSku(sku.id, 'name')} disabled={locked} />
        <button
          type="button"
          className={cx(s.btn, s.btnMedium)}
          title="Lock the spec against accidental edits; click Edit to unlock"
          onClick={() => actions.updateSku(sku.id, { locked: !locked })}
        >
          <LockIcon size={13} />
          {locked ? 'Edit' : 'Lock'}
        </button>
        <button type="button" className={cx(s.save, saved && s.saved)} onClick={saveBag}>
          {saved ? 'Saved ✓' : 'Save bag'}
        </button>
        <button type="button" className={s.btn} onClick={() => actions.dupSku(sku.id)}>
          Duplicate
        </button>
        <button type="button" className={cx(s.btn, s.btnDanger)} onClick={() => actions.delSku(sku.id)}>
          Delete
        </button>
        <button type="button" className={s.btn} title="Print this cost sheet" onClick={() => setTimeout(() => window.print(), 100)}>
          <PrintIcon size={13} />
          Print
        </button>
        <button type="button" className={s.close} title="Close" aria-label="Close" onClick={() => actions.select(null)}>
          ×
        </button>
      </div>

      {locked && (
        <div className={s.lockBanner}>
          <LockIcon size={13} />
          <span>
            Spec is locked — fields are read-only. Click <b>Edit</b> above to unlock.
          </span>
        </div>
      )}

      {r.issues.length > 0 && (
        <ul className={s.issues}>
          {r.issues.map((i, n) => (
            <li key={n} className={cx(s.issue, i.level === 'error' ? s.issueError : s.issueWarn)}>
              <span className={s.issueTag}>{i.level === 'error' ? 'Fix' : 'Check'}</span>
              <span>{i.msg}</span>
            </li>
          ))}
        </ul>
      )}

      <div ref={bodyRef} className={cx(s.body, locked && s.bodyLocked)}>
        <div className={f.form}>
          <JobRow sku={sku} />
          <FabricRow sku={sku} />
          <DimensionsRow sku={sku} result={r} />
          <FilmsSection sku={sku} result={r} />
          <ExtrasSection sku={sku} result={r} />
          <AddonsSection sku={sku} result={r} />
          <OrderQcSection sku={sku} result={r} />
          <MixSection sku={sku} result={r} />
          <ImagesSection sku={sku} />
          <div className={s.echo}>{dimsEcho(sku, r)}</div>
        </div>

        <DetailResults sku={sku} result={r} />
      </div>
    </Card>
  );
}
