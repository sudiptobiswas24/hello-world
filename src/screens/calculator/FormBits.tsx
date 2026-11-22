import { useId } from 'react';
import type { ReactNode } from 'react';
import { cx } from '../../components/ui';
import f from './SpecForm.module.css';

/** Small grey uppercase label (liner / handle / yarn sub-fields) + control. */
export function SubField({ label, className, children }: { label: ReactNode; className?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={f.subLabel}>
        {label}
      </label>
      {children(id)}
    </div>
  );
}

/** Unit-equivalent line under an input; rendered even when empty, as in the design. */
export function Eq({ children }: { children: ReactNode }) {
  return <div className={f.eq}>{children}</div>;
}

/** A labelled group of controls that share one heading (no single input to point at). */
export function Group({
  label,
  small,
  className,
  children,
}: {
  label: ReactNode;
  /** use the small grey sub-label style */
  small?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className={className}>
      <div id={id} className={cx(small ? f.subLabel : f.lbl)}>
        {label}
      </div>
      {children}
    </div>
  );
}

export const toneClass = (t: 'ok' | 'warn' | 'subtle') => (t === 'ok' ? f.toneOk : t === 'warn' ? f.toneWarn : f.toneSubtle);
