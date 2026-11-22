import type { ReactNode } from 'react';
import { cx } from '../../../components/ui';
import s from './Tile.module.css';

/** A wrapping row of tiles; each tile is a term/description pair. */
export function TileStrip({ children }: { children: ReactNode }) {
  return <dl className={s.strip}>{children}</dl>;
}

export interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  /** tooltip explaining the figure */
  title?: string;
  /** brand-tinted tile (primary derived figures) */
  brand?: boolean;
  /** out-of-range background */
  alert?: 'warn' | 'danger' | null;
}

/** Derived-strip tile: tinted, borderless, large value. */
export function StatTile({ label, value, title, brand, alert }: StatTileProps) {
  return (
    <div className={cx(s.stat, brand && s.statBrand, alert === 'warn' && s.statWarn, alert === 'danger' && s.statDanger)} title={title}>
      <dt className={s.label}>{label}</dt>
      <dd className={s.value}>{value}</dd>
    </div>
  );
}

export interface InfoTileProps {
  label: ReactNode;
  value: ReactNode;
  title?: string;
  tone?: 'plain' | 'accent' | 'alert';
  /** secondary line under the value */
  note?: ReactNode;
  noteAlert?: boolean;
}

/** Outlined tile used for order quantities and planning outputs. */
export function InfoTile({ label, value, title, tone = 'plain', note, noteAlert }: InfoTileProps) {
  return (
    <div className={cx(s.info, tone === 'accent' && s.infoAccent, tone === 'alert' && s.infoAlert)} title={title}>
      <dt className={s.label}>{label}</dt>
      <dd className={s.value}>{value}</dd>
      {note != null && note !== '' && <dd className={cx(s.note, noteAlert && s.noteAlert)}>{note}</dd>}
    </div>
  );
}

/** Small grey qualifier after a tile value. */
export function TileSub({ children }: { children: ReactNode }) {
  return <span className={s.sub}>{children}</span>;
}
