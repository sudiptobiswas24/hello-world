import { cx } from './cx';
import s from './Segmented.module.css';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

export interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /** accessible name for the group */
  label: string;
  size?: 'md' | 'sm';
  className?: string;
}

/** A row of mutually exclusive toggle buttons (layout switch, calc mode). */
export function Segmented<T extends string>({ options, value, onChange, label, size = 'md', className }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className={cx(s.group, size === 'sm' && s.small, className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          className={cx(s.option, o.value === value && s.active)}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
