import type { CSSProperties, ReactNode } from 'react';
import { cx } from './cx';
import s from './Feedback.module.css';

export type Tone = 'warn' | 'error' | 'ok' | 'info' | 'neutral';

export interface BannerProps {
  tone: Tone;
  icon?: ReactNode;
  /** 'ruled' sits flush inside a card with a bottom rule; 'boxed' is a standalone rounded box */
  shape?: 'ruled' | 'boxed';
  role?: 'status' | 'alert';
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}

export function Banner({ tone, icon, shape = 'ruled', role, className, style, children }: BannerProps) {
  return (
    <div role={role} className={cx(s.banner, s[tone], shape === 'boxed' ? s.boxed : s.ruled, className)} style={style}>
      {icon}
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function Badge({ tone, className, style, children }: { tone: Tone; className?: string; style?: CSSProperties; children: ReactNode }) {
  return (
    <span className={cx(s.badge, s[tone], className)} style={style}>
      {children}
    </span>
  );
}

/** Coloured status dot; `color` accepts a token like `var(--ok)` or a hex value. */
export function Dot({ color, className }: { color: string; className?: string }) {
  return <span aria-hidden="true" className={cx(s.dot, className)} style={{ background: color }} />;
}

/** Tiny uppercase label over a value (card summaries, cost totals). */
export function Stat({ label, value, strong, className, style }: { label: ReactNode; value: ReactNode; strong?: boolean; className?: string; style?: CSSProperties }) {
  return (
    <div className={cx(s.stat, className)} style={style}>
      <div className={s.statLabel}>{label}</div>
      <div className={cx(s.statValue, strong && s.statValueStrong)}>{value}</div>
    </div>
  );
}
